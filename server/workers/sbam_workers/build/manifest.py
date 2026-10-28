"""The `build_manifest` build stage (E9.1, TD §11): a Sub-Skill's Skill Manifest and its hash.

`build_skill_manifest` is the whole builder and does no I/O. From the stored ISATVON 1.4 Sub-Skill
and the build context it writes the six TD §11.1 blocks:

  * skill_metadata: ids and name from the Sub-Skill (name NFC-normalised); type, version, status,
    owner and timestamps set by SBAM. The timestamps are the Sub-Skill's `validated_at`, never the
    wall clock, so a rebuild of the same ISATVON hashes the same.
  * isatvon_definition: ISA14's flat shape (`to_flat`); the structured form goes to
    `sbam_build_record.isatvon_detail`.
  * srim_runtime_definition: `PLATFORM_SRIM`, with `persistence.table` from V.tables.
  * contracts: `build_contracts` (S.required_inputs; O.json_schema + `state_handoff_id`).
  * governance: lifecycle from the Sub-Skill's state, versioning 1.0.0, and the deterministic hash
    (RFC 8785, `canonical.deterministic_hash`), set by `SkillManifest.sealed`.
  * sbam_build_record: ids, provenance (approved SOW, OKF, master snapshot), template, runtime.

Platform fields are never taken from the input: anything else at the top level of the stored
ISATVON, or a platform key in its `skill_metadata` (e.g. a corrector patch setting `status`), is
dropped and reported, and the activity audits it. A Sub-Skill that fails the TD §10.4 validator
raises `ManifestInvalid`; the activity records an ISATVON_CONTRACT FAILED gate result, writes no
manifest and fails the stage (non-retryable).

Idempotency (TD §13.4): one `sub_skill_manifests` row per stage key (sub_skill_id, build_no, cycle,
attempt); a retried activity returns the row it already wrote.
"""

import asyncio
import copy
import hashlib
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Final

from sbam_api.audit.repository import record
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.isatvon.service import require_approved_sow
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository, SubSkillManifestRepository
from sbam_api.masters.models import MasterSnapshot
from sbam_api.registry.repository import active_registry
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_contracts.isatvon14 import (
    DEFAULT_LIBRARY_REGISTRY,
    FRAMEWORK_VERSION,
    IsatvonDefinition,
    RuleViolation,
    build_contracts,
    validate_sub_skill,
)
from sbam_contracts.isatvon14 import (
    SubSkill as IsatvonSubSkill,
)
from sbam_contracts.manifest import HASH_PLACEHOLDER, SkillManifest, TemplateRef
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import MANIFEST_STATUS, SubSkillState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.codegen.store import load_template
from sbam_workers.registry import activity_for

STAGE: Final = "build_manifest"
MANIFEST_INVALID: Final = "ManifestInvalid"
IDS_MISMATCH: Final = "BuildIdsMismatch"  # the run's tenant / project / workflow ≠ the Sub-Skill's
PROVENANCE_MISSING: Final = "ProvenanceMissing"
TEMPLATE: Final = ("fastmcp-loopy", "1.0.0")
SKILL_TYPE: Final = "Deterministic_Loopy_MCP"  # O-15
PLATFORM_OWNER: Final = "SBAM Platform"
FIRST_VERSION: Final = (1, 0, 0)  # C-32; MAJOR / PATCH bumps come with re-certification (E14)
# ponytail: the ISA14 defaults (TD §11.1) for every tenant; per-tenant SRIM config (O-19) is
# #391. `persistence.table` is filled from the Sub-Skill's V.tables.
PLATFORM_SRIM: Final[Mapping[str, Mapping[str, str]]] = {
    "orchestration": {
        "router": "Dify_Dynamic_Master_Router",
        "trigger": "HTTP_POST_Payload_plus_Variables",
    },
    "execution": {"engine": "FastMCP_Execution_Engine"},
    "gateway": {"route_via": "MCP_Jungle_Gateway_Reverse_Proxy"},
    "persistence": {
        "database": "PostgreSQL_Database_State_Config",
        "action": "Execute_SQL_Queries_via_Postgres_MCP",
    },
    "audit": {"telemetry": "PostgreSQL_Audit_Logs"},
    "ui": {"target": "SRIM_UI_Canvas", "method": "Embedded_iFrames_Widgets"},
}
# ponytail: build_schema_sql (E9.2) is still a stub; until it writes the Workflow's schema.sql
# the manifest records the hash of an empty one.
EMPTY_SCHEMA_SQL_SHA256: Final = hashlib.sha256(b"").hexdigest()
# Set by SBAM only (TD §11.2): never taken from the stored ISATVON.
PLATFORM_METADATA: Final = frozenset(
    {"skill_type", "reusable", "version", "status", "owner", "created_at", "updated_at"}
)
ISATVON_KEYS: Final = frozenset({"skill_metadata", "isatvon_definition"})


class ManifestInvalid(Exception):  # noqa: N818 - the E9.1 spec's name
    """The Sub-Skill fails the TD §10.4 validator (code ISATVON_CONTRACT)."""

    def __init__(self, violations: list[RuleViolation]) -> None:
        super().__init__(f"{len(violations)} ISATVON 1.4 contract finding(s)")
        self.violations = violations


@dataclass(frozen=True)
class BuildContext:
    """Everything the manifest needs besides the ISATVON, loaded by the activity."""

    state: SubSkillState
    validated_at: datetime
    ids: Mapping[str, str]  # tenant_id, project_id, workflow_id, sub_skill_id
    provenance: Mapping[str, Any]  # sbam_build_record.provenance
    template: TemplateRef
    runtime_digest: str
    schema_sql_sha256: str = EMPTY_SCHEMA_SQL_SHA256


def _utc(moment: datetime) -> str:
    """ISO-8601 UTC with "Z". A naive value (SQLite) is UTC, as every SBAM timestamp is stored."""
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def strip_platform_fields(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The stored ISATVON without platform-owned fields, and the dotted paths that were dropped."""
    clean = {k: copy.deepcopy(v) for k, v in raw.items() if k in ISATVON_KEYS}
    rejected = sorted(k for k in raw if k not in ISATVON_KEYS)
    meta = clean.get("skill_metadata")
    if isinstance(meta, dict):
        for key in sorted(PLATFORM_METADATA & meta.keys()):
            del meta[key]
            rejected.append(f"skill_metadata.{key}")
        if isinstance(meta.get("skill_name"), str):
            meta["skill_name"] = unicodedata.normalize("NFC", meta["skill_name"])
    return clean, rejected


def to_flat(definition: IsatvonDefinition) -> dict[str, Any]:
    """ISA14's flat `isatvon_definition` from the structured one: `i_instructions` is the
    statement; the other blocks keep their fields (as stored: unset optional fields stay absent)."""
    flat = definition.model_dump(mode="json", exclude_unset=True)
    flat["i_instructions"] = definition.i_instructions.statement
    return flat


def build_skill_manifest(
    raw: Mapping[str, Any], *, macro_category: str | None, ctx: BuildContext
) -> tuple[SkillManifest, list[str]]:
    """The sealed Skill Manifest for a stored ISATVON Sub-Skill, and the platform fields that were
    dropped from it. Raises ManifestInvalid if the Sub-Skill fails the TD §10.4 validator."""
    clean, rejected = strip_platform_fields(raw)
    violations = validate_sub_skill(
        clean, macro_category=macro_category, library_registry=DEFAULT_LIBRARY_REGISTRY
    )
    if violations:
        raise ManifestInvalid(violations)
    sub = IsatvonSubSkill.model_validate(clean)
    definition, meta = sub.isatvon_definition, sub.skill_metadata
    status = MANIFEST_STATUS[ctx.state]
    stamp = _utc(ctx.validated_at)
    major, minor, patch = FIRST_VERSION
    persistence = {
        **PLATFORM_SRIM["persistence"],
        "table": ", ".join(definition.v_variables.tables),
    }
    manifest = {
        "skill_metadata": {
            "macro_category": meta.macro_category,
            "skill_id": meta.skill_id,
            "skill_name": meta.skill_name,
            "skill_type": SKILL_TYPE,
            "reusable": True,
            "version": f"{major}.{minor}.{patch}",
            "status": status.value,
            "owner": PLATFORM_OWNER,
            "created_at": stamp,
            "updated_at": stamp,
        },
        "isatvon_definition": to_flat(definition),
        "srim_runtime_definition": {**PLATFORM_SRIM, "persistence": persistence},
        "contracts": build_contracts(sub),
        "governance": {
            "deterministic_hash": HASH_PLACEHOLDER,
            "lifecycle": {
                "state": status.value,
                "approval_required_for_change": True,
                "immutable_after_approval": True,
            },
            "versioning": {"major": major, "minor": minor, "patch": patch},
        },
        "sbam_build_record": {
            "isatvon_framework_version": FRAMEWORK_VERSION,
            "isatvon_detail": definition.model_dump(mode="json", exclude_unset=True),
            "ids": dict(ctx.ids),
            "provenance": dict(ctx.provenance),
            "build": {
                "template": ctx.template.model_dump(mode="json"),
                "runtime_image_digest": ctx.runtime_digest,
                "libraries": [
                    lib.model_dump(mode="json", exclude_unset=True)
                    for lib in definition.t_tech_stack.libraries
                ],
                "schema_sql_sha256": ctx.schema_sql_sha256,
            },
        },
    }
    return SkillManifest.sealed(manifest), rejected


# --- the activity ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    raw: dict[str, Any]
    macro_category: str | None
    ctx: BuildContext


def _failure(kind: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


@lru_cache(maxsize=1)
def _template() -> TemplateRef:
    template = load_template(*TEMPLATE)
    if FRAMEWORK_VERSION not in template.supports_isatvon:
        raise _failure(PROVENANCE_MISSING, f"{template.name} {template.version} lacks ISATVON 1.4")
    return template.ref


class SqlManifestStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _ctx(self, inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def existing(self, inp: BuildStageInput) -> uuid.UUID | None:
        with self.sessions() as session:
            row = SubSkillManifestRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            return None if row is None else row.id

    def load(self, inp: BuildStageInput, template: TemplateRef) -> Source:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            # Scoped to the run's tenant and project: another tenant's Sub-Skill is not found.
            sub: SubSkill | None = SubSkillRepository(session, ctx).get(inp.sub_skill_id)
            if sub is None or sub.workflow_id != inp.workflow_id:
                raise _failure(IDS_MISMATCH, f"Sub-Skill {inp.sub_skill_id} is not in this run")
            macro: MacroSkill | None = MacroSkillRepository(session, ctx).get(sub.macro_skill_id)
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            if workflow is None:
                raise _failure(IDS_MISMATCH, f"Workflow {inp.workflow_id} is not in this run")
            try:
                sow = require_approved_sow(session, ctx, workflow)
            except ApiError:
                raise _failure(PROVENANCE_MISSING, "the Workflow's SOW is not approved") from None
            knowledge = KnowledgeVersionRepository(session, ctx).get(sow.knowledge_version_id)
            snapshot = (
                session.get(MasterSnapshot, knowledge.master_snapshot_id)
                if knowledge is not None and knowledge.master_snapshot_id is not None
                else None
            )
            registry = active_registry(session)
            if snapshot is not None and snapshot.tenant_id != inp.tenant_id:
                snapshot = None
            digest = None if registry is None else registry.image_digest
            okf = None if knowledge is None else knowledge.okf_sha256
            if (
                knowledge is None
                or okf is None
                or snapshot is None
                or digest is None
                or sow.sha256 is None
                or sow.approved_at is None
            ):
                missing = {
                    "sow sha256": sow.sha256,
                    "sow approval": sow.approved_at,
                    "okf_sha256": okf,
                    "master snapshot": snapshot,
                    "runtime image digest": digest,
                }
                raise _failure(
                    PROVENANCE_MISSING, ", ".join(k for k, v in missing.items() if v is None)
                )
            return Source(
                raw=dict(sub.isatvon),
                macro_category=None if macro is None else macro.macro_category,
                ctx=BuildContext(
                    state=SubSkillState(sub.state),
                    # rows from before E9.1 have no validated_at: created_at is as stable
                    validated_at=sub.validated_at or sub.created_at,
                    ids={
                        "tenant_id": str(inp.tenant_id),
                        "project_id": str(inp.project_id),
                        "workflow_id": str(inp.workflow_id),
                        "sub_skill_id": str(sub.id),
                    },
                    provenance={
                        "sow_version_id": str(sow.id),
                        "sow_sha256": sow.sha256,
                        "approved_at": _utc(sow.approved_at),
                        "approved_by": sow.approved_by or "unknown",
                        "knowledge_version_id": str(knowledge.id),
                        "okf_sha256": okf,
                        "master_snapshot_sha256": snapshot.sha256,
                        "sow_refs": list(sub.sow_refs),
                    },
                    template=template,
                    runtime_digest=digest,
                ),
            )

    def save(
        self, inp: BuildStageInput, manifest: SkillManifest, digest: str, rejected: list[str]
    ) -> uuid.UUID:
        ctx = self._ctx(inp)
        with self.sessions() as session:
            row = SubSkillManifestRepository(session, ctx).record(
                {
                    "sub_skill_id": inp.sub_skill_id,
                    "build_no": inp.build_no,
                    "cycle": inp.cycle,
                    "attempt": inp.attempt,
                    "manifest": manifest.dump(),
                    "sha256": manifest.governance.deterministic_hash,
                    "runtime_digest": digest,
                    "created_by": "SYSTEM",
                }
            )
            if rejected:
                record(
                    session,
                    tenant_id=inp.tenant_id,
                    actor_id=SYSTEM_ACTOR_ID,
                    action="MANIFEST_PLATFORM_FIELDS_REJECTED",
                    entity_type="sub_skill_manifest",
                    entity_id=str(row.id),
                    after={"sub_skill_id": str(inp.sub_skill_id), "rejected": rejected},
                    correlation_id=inp.correlation_id,
                    idempotency_key=f"{inp.run_id}:{STAGE}:{row.id}:rejected",
                )
            session.commit()
            return row.id

    def gate_failed(self, inp: BuildStageInput, violations: list[RuleViolation]) -> uuid.UUID:
        findings = [
            Finding(
                gate=GateName.ISATVON_CONTRACT, code=v.code, message=f"{v.path}: {v.message}"
            ).model_dump(mode="json", exclude_none=True)
            for v in violations
        ]
        with self.sessions() as session:
            row = GateResultRepository(session, self._ctx(inp)).add(
                GateResultRow(
                    sub_skill_id=inp.sub_skill_id,
                    gate=GateName.ISATVON_CONTRACT.value,
                    status=GateStatus.FAILED.value,
                    findings=findings,
                    created_by="SYSTEM",
                )
            )
            session.commit()
            return row.id


def build_for(store: SqlManifestStore, inp: BuildStageInput) -> IdResult:
    """The whole stage, synchronous (tests call it directly; the activity runs it in a thread)."""
    found = store.existing(inp)
    if found is not None:
        return IdResult(id=found)  # activity retry: the row this stage key already wrote
    src = store.load(inp, _template())
    try:
        manifest, rejected = build_skill_manifest(
            src.raw, macro_category=src.macro_category, ctx=src.ctx
        )
    except ManifestInvalid as exc:
        gate_id = store.gate_failed(inp, exc.violations)
        raise _failure(MANIFEST_INVALID, f"{exc} (gate_results {gate_id})") from None
    return IdResult(id=store.save(inp, manifest, src.ctx.runtime_digest, rejected))


@lru_cache(maxsize=1)
def _store() -> SqlManifestStore:
    return SqlManifestStore(mirror.sessions())


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def build_manifest(inp: BuildStageInput) -> IdResult:
    """The Sub-Skill's manifest + ISATVON hash (a new hash means a re-render)."""
    return await asyncio.to_thread(build_for, _store(), inp)
