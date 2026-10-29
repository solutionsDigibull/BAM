"""The `build_workflow_manifest` stage (E9.5, TD §11.3): the Workflow manifest draft of a build.

`draft` is the whole builder and does no I/O. It checks the Agent / Sub-Skill set (unique
`MSnn_<Name>` categories, each with at least one Sub-Skill, each Sub-Skill with a manifest),
needs the Workflow's PASSED schema.sql (else SCHEMA_SQL_MISSING), builds the DAG
(`dag.build_dag`, DAG_CYCLE on a loop) and seals the manifest: `integrity.content_sha256` is
RFC 8785 + SHA-256 over every skill's deterministic, manifest and bundle hash and the schema SQL
hash. A draft has no `certification`; bundle hashes are null before codegen and certification ids
before E14. Agents and Sub-Skills are sorted by id, so database order never changes the bytes.

The activity loads everything through tenant-scoped repositories and cross-checks the ids: a
Sub-Skill whose Agent isn't one of this Workflow's is rejected (`IdsMismatch`) and written to the
audit log. One `workflow_manifests` row per (workflow_id, build_no), where build_no is the newest
build among the Sub-Skill manifests; a retried activity returns the row it already wrote. The draft
is never exported or frozen here.
"""

import asyncio
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from sbam_api.audit.repository import record
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.manifests.repository import SubSkillManifestRepository, WorkflowManifestRepository
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.api import ErrorCode
from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.isatvon14 import FRAMEWORK_VERSION
from sbam_contracts.manifest import WORKFLOW_SCHEMA_VERSION, WorkflowManifest
from sbam_contracts.pipeline import IdResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.dag import DraftInvalid, Node, build_dag
from sbam_workers.build.manifest import IDS_MISMATCH, load_provenance
from sbam_workers.registry import activity_for

STAGE: Final = "build_workflow_manifest"
MACRO_CATEGORY: Final = re.compile(r"^MS\d{2}_[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Agent:
    macro_category: str
    name: str
    agent_ref: str | None


@dataclass(frozen=True)
class Skill:
    node: Node
    manifest_sha256: str | None  # governance.deterministic_hash of its newest manifest
    bundle_sha256: str | None  # the newest assembled bundle; null before codegen


@dataclass(frozen=True)
class DraftInputs:
    ids: Mapping[str, str]  # tenant_id, project_id, workflow_id
    name: str
    objective: str
    version: str
    previous_version: str | None
    provenance: Mapping[str, Any]
    agents: Sequence[Agent]
    skills: Sequence[Skill]
    schema_sql: tuple[str, str] | None  # (artifact_key, sha256) of the PASSED schema.sql
    runtime_digest: str


def draft(inp: DraftInputs) -> WorkflowManifest:
    """The sealed draft; DraftInvalid when the Workflow can't have one."""
    categories = [a.macro_category for a in inp.agents]
    for category in categories:
        if MACRO_CATEGORY.match(category) is None:
            raise DraftInvalid(ErrorCode.ISATVON_CONTRACT, f"{category!r} is not MSnn_<Name>")
    if len(set(categories)) != len(categories):
        raise DraftInvalid(ErrorCode.ISATVON_CONTRACT, "two Agents share a macro_category")
    by_agent: dict[str, list[Skill]] = {c: [] for c in categories}
    for skill in inp.skills:
        if skill.node.macro_category not in by_agent:
            raise DraftInvalid(ErrorCode.ISATVON_CONTRACT, f"{skill.node.skill_id}: unknown Agent")
        if skill.manifest_sha256 is None:
            raise DraftInvalid(ErrorCode.NOT_FOUND, f"{skill.node.skill_id} has no manifest yet")
        by_agent[skill.node.macro_category].append(skill)
    for category, skills in by_agent.items():
        if not skills:
            raise DraftInvalid(ErrorCode.MACRO_REQUIRES_SUB_SKILL, f"{category} has no Sub-Skill")
    if inp.schema_sql is None:
        raise DraftInvalid(ErrorCode.SCHEMA_SQL_MISSING, "the Workflow has no PASSED schema.sql")
    graph = build_dag([s.node for s in inp.skills])
    artifact_key, schema_sha256 = inp.schema_sql
    agents = sorted(inp.agents, key=lambda a: a.macro_category)
    return WorkflowManifest.sealed(
        {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "ids": dict(inp.ids),
            "name": inp.name,
            "objective": inp.objective,
            "version": inp.version,
            "previous_version": inp.previous_version,
            "provenance": dict(inp.provenance),
            "isatvon_framework_version": FRAMEWORK_VERSION,
            "macro_skills": [
                {
                    "macro_category": a.macro_category,
                    "name": a.name,
                    "agent_ref": a.agent_ref,
                    "certification_id": None,
                    "skills": [
                        {
                            "skill_id": s.node.skill_id,
                            "deterministic_hash": s.manifest_sha256,
                            "manifest_sha256": s.manifest_sha256,
                            "bundle_sha256": s.bundle_sha256,
                            "certification_id": None,
                        }
                        for s in sorted(by_agent[a.macro_category], key=lambda s: s.node.skill_id)
                    ],
                }
                for a in agents
            ],
            "schema_sql": {"artifact_key": artifact_key, "sha256": schema_sha256},
            "dag": {"nodes": graph.nodes, "edges": graph.edges},
            "runtime_image_digest": inp.runtime_digest,
            "evidence_index": [],
            "integrity": {"content_sha256": ""},  # set by sealed()
        }
    )


# --- the activity ---------------------------------------------------------------------------------


def _failure(kind: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


def _previous_version(session: Session, ctx: TenantContext, workflow: Workflow) -> str | None:
    """The version of the MAJOR before this one (a scope change), if there was one."""
    majors = {w.major: w.version for w in WorkflowRepository(session, ctx).of_project()}
    return majors.get(workflow.major - 1)


class SqlWorkflowManifestStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _ctx(self, inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def load(self, inp: WorkflowRef) -> tuple[DraftInputs, int]:
        """The draft's inputs and the build number it belongs to."""
        ctx = self._ctx(inp)
        with self.sessions() as session:
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            if workflow is None:
                raise _failure(IDS_MISMATCH, f"Workflow {inp.workflow_id} is not in this run")
            prov = load_provenance(session, ctx, workflow)
            macros = MacroSkillRepository(session, ctx).of_workflow(inp.workflow_id)
            subs = SubSkillRepository(session, ctx).of_workflow(inp.workflow_id)
            category = {m.id: m.macro_category for m in macros}
            foreign = [s for s in subs if s.macro_skill_id not in category]
            if foreign:
                self._reject(session, inp, [s.id for s in foreign])
                raise _failure(IDS_MISMATCH, f"{len(foreign)} Sub-Skill(s) outside this Workflow")
            manifests = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
            attempts = CodeAttemptRepository(session, ctx)
            skills = []
            for sub in subs:
                assembled = [
                    a.bundle_sha256
                    for a in attempts.of_sub_skill(inp.workflow_id, sub.id)
                    if a.status == CodeAttemptStatus.ASSEMBLED.value and a.bundle_sha256
                ]
                found = manifests.get(sub.id)
                skills.append(
                    Skill(
                        node=Node(sub.id, sub.skill_id, category[sub.macro_skill_id], sub.isatvon),
                        manifest_sha256=None if found is None else found.sha256,
                        bundle_sha256=assembled[-1] if assembled else None,
                    )
                )
            schema = WorkflowSchemaRepository(session, ctx).latest(
                inp.workflow_id, status=GateStatus.PASSED
            )
            structured = prov.sow.structured or {}
            overview = structured.get("overview")
            objective = overview.get("objective") if isinstance(overview, Mapping) else None
            sow_refs = sorted({ref for m in macros for ref in m.sow_refs})
            build_no = max((m.build_no for m in manifests.values()), default=0)
            return (
                DraftInputs(
                    ids={
                        "tenant_id": str(inp.tenant_id),
                        "project_id": str(inp.project_id),
                        "workflow_id": str(inp.workflow_id),
                    },
                    name=workflow.name,
                    objective=objective if isinstance(objective, str) and objective else "-",
                    version=workflow.version,
                    previous_version=_previous_version(session, ctx, workflow),
                    provenance={**prov.fields, "sow_refs": sow_refs},
                    agents=[Agent(m.macro_category, m.name, m.agent_ref) for m in macros],
                    skills=skills,
                    schema_sql=(
                        (schema.storage_key, schema.sha256)
                        if schema is not None and schema.storage_key and schema.sha256
                        else None
                    ),
                    runtime_digest=prov.runtime_digest,
                ),
                build_no,
            )

    def _reject(self, session: Session, inp: WorkflowRef, sub_skill_ids: list[uuid.UUID]) -> None:
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action="WORKFLOW_MANIFEST_REF_REJECTED",
            entity_type="workflow",
            entity_id=str(inp.workflow_id),
            after={"sub_skill_ids": sorted(map(str, sub_skill_ids))},
            correlation_id=inp.correlation_id,
            idempotency_key=f"{inp.run_id}:{STAGE}:{inp.workflow_id}:rejected",
        )
        session.commit()

    def save(self, inp: WorkflowRef, build_no: int, manifest: WorkflowManifest) -> uuid.UUID:
        with self.sessions() as session:
            row = WorkflowManifestRepository(session, self._ctx(inp)).record(
                {
                    "workflow_id": inp.workflow_id,
                    "build_no": build_no,
                    "manifest": manifest.dump(),
                    "sha256": manifest.integrity.content_sha256,
                    "created_by": "SYSTEM",
                }
            )
            session.commit()
            return row.id

    def existing(self, inp: WorkflowRef, build_no: int) -> uuid.UUID | None:
        with self.sessions() as session:
            row = WorkflowManifestRepository(session, self._ctx(inp)).find(
                inp.workflow_id, build_no
            )
            return None if row is None else row.id


def build_for(store: SqlWorkflowManifestStore, inp: WorkflowRef) -> IdResult:
    """The whole stage, synchronous (tests call it directly; the activity runs it in a thread)."""
    inputs, build_no = store.load(inp)
    found = store.existing(inp, build_no)
    if found is not None:
        return IdResult(id=found)  # activity retry: this build's draft is already written
    try:
        manifest = draft(inputs)
    except DraftInvalid as exc:
        raise _failure(exc.kind, f"{exc.code.value}: {exc}") from None
    return IdResult(id=store.save(inp, build_no, manifest))


@lru_cache(maxsize=1)
def _store() -> SqlWorkflowManifestStore:
    return SqlWorkflowManifestStore(mirror.sessions())


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def build_workflow_manifest(inp: WorkflowRef) -> IdResult:
    """The Workflow manifest draft of this build (never exported or frozen here)."""
    return await asyncio.to_thread(build_for, _store(), inp)

