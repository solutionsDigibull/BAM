"""`check_libraries` (E9.3, TD §10.4 rule 9): a Sub-Skill's libraries against the runtime registry.

The SubSkillBuild stage after `build_manifest`. It checks the libraries the manifest declares
(`sbam_build_record.build.libraries`, from `t_tech_stack.libraries`) against the registry of the
skill-runtime image the manifest pinned at the build's start (`sub_skill_manifests.runtime_digest`:
a registry activated mid-build changes nothing for this build). Lookup only: nothing is installed.

* Names are compared after PEP 503 normalization only (`Pydantic_Core` is `pydantic-core`); there is
  no fuzzy matching that could substitute another package (typosquatting).
* A package is FORBIDDEN when it is another database engine or driver (PostgreSQL only), even when
  the registry has it; MISSING when the registry has no such allowed package; VERSION_CONFLICT when
  no registry version satisfies its PEP 440 constraint (or the constraint does not parse, or it is
  declared twice). The template's own packages (`TEMPLATE_PACKAGES`) are always there.
* The guidance is a fixed template per code (edit T, or ask the platform to add the library),
  never generated text and never an install command.
* Findings and pins are sorted by package, so the same libraries in another order give the same
  result.

It writes one `gate_results` row (gate LIBRARIES, tool_version = the registry digest) per build
stage key; a retried activity finds it and answers the same. PASSED: the manifest's
`build.libraries` is pinned to the registry versions (`==<version>`) and the manifest sealed again;
those pins feed the code generator's import allowlist. FAILED: `LibraryCheckResult(MISSING,
[gate row id])`, which the workflow turns into DEPENDENCY_FAILED and a correction;
`render_skeleton` is never reached.
No registry for the pinned digest: a FAILED row with code REGISTRY_UNAVAILABLE, an operator alert
in the log, and a non-retryable `RegistryUnavailable` error (no codegen, no correction).
"""

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository, SubSkillManifestRepository
from sbam_api.registry.repository import registry_for_digest
from sbam_contracts.build import (
    REGISTRY_UNAVAILABLE,
    BuildStageInput,
    LibraryCheckResult,
    LibraryStatus,
)
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_contracts.isatvon14 import FORBIDDEN_LIBRARIES, normalize_package
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.runs import TaskQueue
from sbam_contracts.runtime_registry import RuntimeRegistry
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.build.libraries")

STAGE: Final = "check_libraries"
GATE: Final = GateName.LIBRARIES
MISSING: Final = "MISSING"
VERSION_CONFLICT: Final = "VERSION_CONFLICT"
FORBIDDEN: Final = "FORBIDDEN"
NO_REGISTRY: Final = "REGISTRY_UNAVAILABLE"
# PostgreSQL only (TD §10.4 rule 9): other engines and their drivers, however they are spelled.
FORBIDDEN_PACKAGES: Final = frozenset(
    {
        *FORBIDDEN_LIBRARIES,
        "aiosqlite",
        "apsw",
        "pysqlite3",
        "pysqlite3-binary",
        "sqlite-utils",
        "pymysql",
        "mysqlclient",
        "mysql-connector-python",
        "aiomysql",
        "pymongo",
        "motor",
        "cx-oracle",
        "oracledb",
        "pyodbc",
        "pymssql",
        "cassandra-driver",
        "couchbase",
        "clickhouse-driver",
        "snowflake-connector-python",
    }
)
# What the code template itself imports: always present, whatever the Sub-Skill declares.
TEMPLATE_PACKAGES: Final = frozenset({"fastmcp", "pydantic"})
GUIDANCE: Final = {
    MISSING: (
        "{package} is not in the skill runtime. Edit the Sub-Skill's T (tech stack) to use a "
        "library the runtime has, or ask the platform team to add {package} to the runtime image."
    ),
    VERSION_CONFLICT: (
        "The runtime has {package} {available}, which does not satisfy '{requested}'. Edit the "
        "Sub-Skill's T (tech stack) to accept {available}, or ask the platform team for a version "
        "that matches."
    ),
    FORBIDDEN: (
        "{package} is another database engine or driver: SBAM skills use PostgreSQL only. Edit "
        "the Sub-Skill's T (tech stack) to use psycopg."
    ),
    NO_REGISTRY: (
        "The skill runtime image this build pinned has no library registry. The platform team "
        "must sync it (sbam_api.registry.sync); the build stops until then."
    ),
}
_VALID_NAME = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")


@dataclass(frozen=True)
class Outcome:
    findings: list[Finding]
    pins: list[dict[str, str]]  # [{package, version_constraint: "==<version>"}], sorted

    @property
    def passed(self) -> bool:
        return not self.findings


# --- the check (pure) -----------------------------------------------------------------------------


def _finding(code: str, package: str, requested: str, available: str | None) -> Finding:
    message = GUIDANCE[code].format(package=package, requested=requested, available=available)
    return Finding(
        gate=GATE,
        code=code,
        message=message,
        package=package,
        expected=requested,
        actual=available,
    )


def _requested(lib: dict[str, Any]) -> str:
    constraint = str(lib.get("version_constraint") or "").strip()
    return f"{lib.get('package', '')}{constraint}"


def check(libraries: Iterable[dict[str, Any]], registry: RuntimeRegistry) -> Outcome:
    """Every declared library against `registry`; see the module docstring."""
    findings: list[Finding] = []
    pins: dict[str, str] = {}
    seen: set[str] = set()
    for lib in sorted(libraries, key=lambda x: normalize_package(str(x.get("package", "")))):
        name = normalize_package(str(lib.get("package", "")))
        requested = _requested(lib)
        constraint = str(lib.get("version_constraint") or "").strip()
        if name in seen:
            findings.append(_finding(VERSION_CONFLICT, name, requested, None))
            continue
        seen.add(name)
        if name in FORBIDDEN_PACKAGES:
            findings.append(_finding(FORBIDDEN, name, requested, None))
            continue
        found = registry.package(name) if _VALID_NAME.match(name) else None
        if found is None or not found.allowed:
            if name in TEMPLATE_PACKAGES:
                continue  # provided by the template; nothing to pin
            findings.append(_finding(MISSING, name, requested, None))
            continue
        try:
            fits = not constraint or Version(found.version) in SpecifierSet(constraint)
        except (InvalidSpecifier, InvalidVersion):
            fits = False
        if not fits:
            findings.append(_finding(VERSION_CONFLICT, name, requested, found.version))
            continue
        pins[name] = f"=={found.version}"
    return Outcome(
        findings=sorted(findings, key=lambda f: (f.package or "", f.code)),
        pins=[{"package": p, "version_constraint": v} for p, v in sorted(pins.items())],
    )


# --- the stage ------------------------------------------------------------------------------------


class LibraryStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _ctx(inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def run(self, inp: BuildStageInput) -> LibraryCheckResult:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            gates = GateResultRepository(session, ctx)
            key = (inp.sub_skill_id, GATE.value, inp.build_no, inp.cycle, inp.attempt)
            done = gates.for_stage(*key)
            if done is not None:  # activity retry: the same answer, nothing written again
                return _answer(inp, done)
            manifests = SubSkillManifestRepository(session, ctx)
            row = manifests.find(inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt)
            if row is None:
                raise ApplicationError(
                    "check_libraries ran before build_manifest", type="ManifestMissing",
                    non_retryable=True,
                )
            registry = registry_for_digest(session, row.runtime_digest)
            declared = row.manifest["sbam_build_record"]["build"]["libraries"]
            if registry is None:
                finding = _finding(NO_REGISTRY, "", "", None)
                gate = self._record(gates, inp, [finding], row.runtime_digest)
                session.commit()
                return _answer(inp, gate)
            outcome = check(declared, registry)
            gate = self._record(gates, inp, outcome.findings, registry.image_digest or "")
            if outcome.passed:
                data = dict(row.manifest)
                data["sbam_build_record"] = dict(data["sbam_build_record"])
                data["sbam_build_record"]["build"] = {
                    **data["sbam_build_record"]["build"],
                    "libraries": outcome.pins,
                }
                sealed = SkillManifest.sealed(data)
                row.manifest = sealed.dump()
                row.sha256 = sealed.governance.deterministic_hash
                manifests.update(row, row.row_version)
            session.commit()
            return _answer(inp, gate)

    @staticmethod
    def _record(
        gates: GateResultRepository, inp: BuildStageInput, findings: list[Finding], digest: str
    ) -> GateResultRow:
        return gates.record_stage(
            {
                "sub_skill_id": inp.sub_skill_id,
                "gate": GATE.value,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt,
                "status": (GateStatus.FAILED if findings else GateStatus.PASSED).value,
                "findings": [f.model_dump(mode="json", exclude_none=True) for f in findings],
                "tool_version": digest[:80] or None,
                "created_by": "SYSTEM",
            }
        )


def _answer(inp: BuildStageInput, gate: GateResultRow) -> LibraryCheckResult:
    """The stage's result from its gate row (the first run's or a retry's)."""
    if any(f.get("code") == NO_REGISTRY for f in gate.findings):
        log.error(
            "no runtime registry for the pinned image digest; the build stops",
            extra={
                "alert": "RUNTIME_REGISTRY_UNAVAILABLE",
                "tenant_id": str(inp.tenant_id),
                "project_id": str(inp.project_id),
                "sub_skill_id": str(inp.sub_skill_id),
                "runtime_digest": gate.tool_version,
                "correlation_id": inp.correlation_id,
            },
        )
        raise ApplicationError(
            "no runtime registry for the pinned image digest",
            str(gate.id),
            type=REGISTRY_UNAVAILABLE,
            non_retryable=True,
        )
    if gate.status == GateStatus.PASSED.value:
        return LibraryCheckResult(status=LibraryStatus.OK)
    return LibraryCheckResult(status=LibraryStatus.MISSING, finding_ids=[uuid.UUID(str(gate.id))])


@lru_cache(maxsize=1)
def _store() -> LibraryStore:
    return LibraryStore(mirror.sessions())


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
def check_libraries(inp: BuildStageInput) -> LibraryCheckResult:
    """The Sub-Skill's libraries against the pinned runtime registry (E9.3)."""
    return _store().run(inp)
