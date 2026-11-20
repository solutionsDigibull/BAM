"""The `compliance_gates` build stage (#761): licence, dependency and MCP evidence to certify.

Certification needs three results on the same bundle hash besides the gates of E12 (ADR 0028,
decisions 2 and 3). This stage records each as a `gate_results` row, like every other gate, so the
dashboard shows them, the corrector can read them, and `certify.checklist` requires them
(`REQUIRED_GATES` is every `GateName` it does not exempt):

  REPO_LICENCE     the licence of each repository the Sub-Skill starts from and of its
                   dependencies, from the stored scan report of #755 (`RepoScanReport`). A
                   repository without a report fails closed; so does a refused fetch or a licence
                   that is not on the allowlist and has no recorded override.
  DEPENDENCY_VULN  the known vulnerabilities of those repositories. HIGH and CRITICAL fail the
                   gate; MEDIUM, LOW and UNKNOWN are reported and never block (Bandit's rule).
  MCP_COMPLIANCE   the Sub-Skill's two tools can be MCP tools: legal names (`MSnn_m__action` and
                   `MSnn_m__check`), two different tools (C-50), valid JSON Schemas.

A Sub-Skill that starts from no repository has no third-party code: its licence and vulnerability
rows are recorded PASSED with a tool version that says so, so a missing row always means "not run".

There is no import evidence any more (#922, ADR 0040): an import SBAM did not declare is neither
resolved nor scanned, and the stage no longer waits for an `import_gate` row. A failing row is a
`GateFailed` with the attempt id, like any gate, so the
corrector reads the findings from `gate_results`. Findings name repositories, packages and rules,
never code.
"""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.storage.base import Kind, ListableStorage, StorageBackend, StorageScope
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.manifest import McpToolEntry
from sbam_contracts.pipeline import IdResult
from sbam_contracts.repo_scan import (
    LICENCE_ALLOWLIST,
    FetchDecision,
    LicenceVerdict,
    RepoScanReport,
)
from sbam_contracts.repo_scan import Severity as VulnSeverity
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH
from sbam_workers.build.gates.static_tools import finding
from sbam_workers.build.gates.stored_bundle import (
    Verdict,
    fail,
    find_assembled,
    read_verified,
    record_verdicts,
)

STAGE = "compliance_gates"
GATE_VERSION: Final = "compliance/1.0.0"
NO_REPOSITORY: Final = f"{GATE_VERSION} no-third-party-repository"
CONFIG_SHA256: Final = (
    "sha256:" + hashlib.sha256((GATE_VERSION + ",".join(LICENCE_ALLOWLIST)).encode()).hexdigest()
)
MAX_FINDINGS: Final = 100
MAX_REPORT_BYTES: Final = 4 * 1024 * 1024  # a stored tree is a tar, far larger: not a report
MAX_REPORTS_READ: Final = 500
_SKILL_ID = re.compile(r"^MS(\d{2})\.(\d+)_")
BLOCKING: Final = frozenset({Severity.HIGH})


@dataclass(frozen=True)
class RepoRef:
    """A repository the Sub-Skill's T block names: where the code starts from."""

    url: str
    commit: str | None


# --- the rules (pure) -------------------------------------------------------------------------


def source_repos(manifest: Mapping[str, Any]) -> list[RepoRef]:
    """`source_repos` of the manifest's T block; none when the Sub-Skill uses no repository."""
    tech = manifest.get("isatvon_definition", {}).get("t_tech_stack", {})
    return [
        RepoRef(str(repo["url"]), repo.get("commit") or None)
        for repo in (tech.get("source_repos") or [])
    ]


def _name(repo: RepoRef) -> str:
    return f"{repo.url}@{repo.commit[:12]}" if repo.commit else f"{repo.url} (not pinned)"


def licence_findings(repo: RepoRef, report: RepoScanReport | None) -> list[dict[str, Any]]:
    gate, high, medium = GateName.REPO_LICENCE, Severity.HIGH, Severity.MEDIUM
    where = _name(repo)
    if report is None:
        message = f"{where}: no scan report (the repository was not fetched and checked)"
        return [finding(gate, "REPO_SCAN_MISSING", repo.url, None, high, message)]
    found: list[dict[str, Any]] = []
    if report.decision is not FetchDecision.VENDORED:
        reasons = ", ".join(r.value for r in report.refusals) or "refused"
        found.append(finding(gate, "REPO_REFUSED", repo.url, None, high, f"{where}: {reasons}"))
    licence = report.licence
    if licence is None or (
        licence.verdict is not LicenceVerdict.ALLOWED and licence.override is None
    ):
        spdx = licence.spdx if licence is not None and licence.spdx else "unknown"
        message = f"{where}: licence {spdx} is not on the allowlist and has no recorded override"
        found.append(finding(gate, "REPO_LICENCE_NOT_ALLOWED", repo.url, None, high, message))
    for dep in report.dependency_licences:
        if dep.verdict is LicenceVerdict.ALLOWED:
            continue
        refused = dep.verdict is LicenceVerdict.REFUSED  # copyleft blocks; unknown is reported
        code = "DEPENDENCY_LICENCE_REFUSED" if refused else "DEPENDENCY_LICENCE_UNKNOWN"
        message = (
            f"{where}: {dep.ecosystem} {dep.name} {dep.version} licence {dep.spdx or 'unknown'}"
        )
        found.append(finding(gate, code, repo.url, None, high if refused else medium, message))
    return found


_SEVERITY: Final = {
    VulnSeverity.CRITICAL: Severity.HIGH,
    VulnSeverity.HIGH: Severity.HIGH,
    VulnSeverity.MEDIUM: Severity.MEDIUM,
    VulnSeverity.LOW: Severity.LOW,
    VulnSeverity.UNKNOWN: Severity.LOW,
}


def vulnerability_findings(repo: RepoRef, report: RepoScanReport | None) -> list[dict[str, Any]]:
    if report is None:
        message = f"{_name(repo)}: no scan report, so no vulnerability result"
        return [
            finding(
                GateName.DEPENDENCY_VULN,
                "REPO_SCAN_MISSING",
                repo.url,
                None,
                Severity.HIGH,
                message,
            )
        ]
    return [
        finding(
            GateName.DEPENDENCY_VULN,
            "DEPENDENCY_VULNERABILITY",
            repo.url,
            None,
            _SEVERITY[v.severity],
            f"{_name(repo)}: {v.id} in {v.ecosystem} {v.package} {v.version}",
        )
        for v in report.vulnerabilities
    ]


def _schema_problem(schema: Mapping[str, Any]) -> str | None:
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as exc:
        return str(exc.message)[:200]
    return None


def mcp_findings(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Can this Sub-Skill's Action and Fixed Check tools be tools of the Workflow's MCP server?"""
    gate, high = GateName.MCP_COMPLIANCE, Severity.HIGH
    file = "manifest.json"
    skill_id = str(manifest.get("skill_metadata", {}).get("skill_id", ""))
    tech = manifest.get("isatvon_definition", {}).get("t_tech_stack", {})
    contracts = manifest.get("contracts", {})
    found: list[dict[str, Any]] = []
    if tech.get("action_tool") and tech.get("action_tool") == tech.get("fixed_check_tool"):
        message = "the Action tool and the Fixed Check tool are the same tool (C-50)"
        found.append(finding(gate, "MCP_TOOLS_NOT_INDEPENDENT", file, None, high, message))
    problems = {
        "input": _schema_problem(contracts.get("input_schema", {})),
        "output": _schema_problem(contracts.get("output_schema", {})),
    }
    for side, problem in problems.items():
        if problem is not None:
            message = f"the {side} schema is not a valid JSON Schema: {problem}"
            found.append(
                finding(gate, f"MCP_{side.upper()}_SCHEMA_INVALID", file, None, high, message)
            )
    skill = _SKILL_ID.match(skill_id)
    if skill is None:
        found.append(
            finding(gate, "MCP_TOOL_NAME", file, None, high, "skill_id is not MSnn.m_<Name>")
        )
        return found
    for role in ("action", "check"):
        name = f"MS{skill.group(1)}_{skill.group(2)}__{role}"
        try:
            McpToolEntry(
                name=name,
                skill_id=skill_id,
                role=role,
                input_schema=dict(contracts.get("input_schema", {})),
                output_schema=dict(contracts.get("output_schema", {})),
            )
        except ValidationError as exc:
            detail = exc.errors()[0]["msg"][:150] if exc.errors() else "invalid"
            found.append(finding(gate, "MCP_TOOL_NAME", file, None, high, f"{name}: {detail}"))
    return sorted(found, key=lambda f: (f["code"], f["message"]))


def status_of(found: Sequence[Mapping[str, Any]]) -> GateStatus:
    blocking = any(Severity(f["severity"]) in BLOCKING for f in found)
    return GateStatus.FAILED if blocking else GateStatus.PASSED


def verdicts_of(
    manifest: Mapping[str, Any], reports: Mapping[RepoRef, RepoScanReport | None]
) -> list[Verdict]:
    """The three rows, from the manifest and the reports found for its repositories."""
    repos = source_repos(manifest)
    licence: list[dict[str, Any]] = []
    vulnerable: list[dict[str, Any]] = []
    for repo in repos:
        licence += licence_findings(repo, reports.get(repo))
        vulnerable += vulnerability_findings(repo, reports.get(repo))
    version = GATE_VERSION if repos else NO_REPOSITORY
    mcp = mcp_findings(manifest)
    out: list[Verdict] = []
    for gate, found, tool in (
        (GateName.REPO_LICENCE, licence, version),
        (GateName.DEPENDENCY_VULN, vulnerable, version),
        (GateName.MCP_COMPLIANCE, mcp, GATE_VERSION),
    ):
        ordered = sorted(found, key=lambda f: (f["file"], f["code"], f["message"]))[:MAX_FINDINGS]
        out.append(Verdict(gate, status_of(found), ordered, tool, CONFIG_SHA256))
    return out


# --- where the reports are ----------------------------------------------------------------------

ReportFinder = Callable[
    [BuildStageInput, Sequence[RepoRef]], Mapping[RepoRef, RepoScanReport | None]
]


def storage_report_finder(storage: StorageBackend) -> ReportFinder:
    """Finds the newest stored `RepoScanReport` (#755, kind `repos`) of each repository by URL and
    pinned commit. A backend that cannot list its keys, or a repository with no commit, finds none:
    the licence row then fails closed instead of passing on nothing."""

    def find(
        inp: BuildStageInput, repos: Sequence[RepoRef]
    ) -> Mapping[RepoRef, RepoScanReport | None]:
        wanted = {r: None for r in repos if r.commit}
        if not wanted or not hasattr(storage, "list_keys"):
            return {r: None for r in repos}
        listable: ListableStorage = storage  # type: ignore[assignment]
        prefix = StorageScope(inp.tenant_id, inp.project_id).key(Kind.REPOS, "").rstrip("/") + "/"
        newest: dict[RepoRef, tuple[Any, RepoScanReport]] = {}
        for n, item in enumerate(listable.list_keys(prefix)):
            if n >= MAX_REPORTS_READ:
                break
            if item.size > MAX_REPORT_BYTES:
                continue
            try:
                report = RepoScanReport.model_validate_json(storage.get(item.storage_key))
            except (ValidationError, ValueError):
                continue  # a vendored tree (a tar) or something else
            ref = RepoRef(report.url, report.commit)
            if ref in wanted and (ref not in newest or item.modified_at > newest[ref][0]):
                newest[ref] = (item.modified_at, report)
        return {r: (newest[r][1] if r in newest else None) for r in repos}

    return find


ManifestOf = Callable[[BuildStageInput, uuid.UUID], Mapping[str, Any]]


class ComplianceGates:
    """`compliance_gates` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        storage: StorageBackend,
        sessions: sessionmaker[Session],
        reports: ReportFinder | None = None,
        manifest_of: ManifestOf | None = None,
    ) -> None:
        self.storage = storage
        self.sessions = sessions
        self.reports = reports or storage_report_finder(storage)
        self.manifest_of = manifest_of or self._manifest

    def _manifest(self, inp: BuildStageInput, attempt_id: uuid.UUID) -> dict[str, Any]:
        ctx = TenantContext(inp.tenant_id, inp.project_id)
        with self.sessions() as session:
            attempt = CodeAttemptRepository(session, ctx).get(attempt_id)
            row = (
                None
                if attempt is None or attempt.manifest_id is None
                else SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)
            )
            if row is None:
                raise ApplicationError(
                    f"no manifest for code attempt {attempt_id}",
                    type=GATE_INPUT_MISMATCH,
                    non_retryable=True,
                )
            return dict(row.manifest)

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        return await asyncio.to_thread(self.execute, inp)

    def execute(self, inp: BuildStageInput) -> IdResult:
        assembled = find_assembled(self.sessions, inp)
        bundle = read_verified(self.storage, assembled)
        manifest = self.manifest_of(inp, assembled.attempt_id)
        repos = source_repos(manifest)
        verdicts = verdicts_of(manifest, self.reports(inp, repos) if repos else {})
        statuses = record_verdicts(self.sessions, inp, bundle, verdicts)
        if any(s is not GateStatus.PASSED for s in statuses):
            failed = [
                v.gate.value
                for v, s in zip(verdicts, statuses, strict=True)
                if s is not GateStatus.PASSED
            ]
            activity.logger.info(
                "a compliance gate failed",
                extra={
                    "tenant_id": str(inp.tenant_id),
                    "project_id": str(inp.project_id),
                    "sub_skill_id": str(inp.sub_skill_id),
                    "gates": json.dumps(failed),
                    "correlation_id": inp.correlation_id,
                },
            )
            raise fail("generated code failed a compliance gate", assembled.attempt_id, GATE_FAILED)
        return IdResult(id=assembled.attempt_id)
