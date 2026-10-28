"""`extract_macro_skills` (E8.2, TD §10.2, §13.2): ISATVON pass 1, the Workflow's Macro Skills.

`extract_macros` is the whole step and takes its dependencies as arguments (the SOW step's
`SowDeps`), so tests run it with a fake Bifrost and a temp-dir storage. For Workflow `inp`:

  1. Macro Skills already stored for the Workflow are returned as they are (activity retry): one
     set per Workflow, never a second one. After a scope change (E7.2) the Workflow starts with the
     Macro Skills carried over from the previous version; SOW modules none of them covers (new
     ones) are then extracted on their own (`scope`), numbered after the existing Macro Skills.
  2. The Workflow's SOW version must still be APPROVED and the SOW's approved version
     (`require_approved_sow`), else `SowNotApproved`. A SOW with more than 99 modules can't be
     numbered (`MSnn`), so it fails before any model call (`TooManyModules`).
  3. Context: the approved SowDocument, the Agents-master entries of the knowledge version's
     master snapshot (`AG-*`; illustrative, retired and, unless the project allows them,
     restricted entries are left out) and the OKF bundle the SOW was written from: whole when it
     fits, else ranked per SOW module (`sow/context.py`), recorded in `llm_calls.context_manifest`.
  4. `sbam-isatvon` answers a `MacroExtraction`. Beyond the schema, every SOW module must appear in
     some `sow_refs` (`MODULE_NOT_COVERED`) and every ref must be a module or feature id of the SOW
     (`UNKNOWN_SOW_REF`); a failed check is re-asked like a schema error (codes and ids only).
  5. SBAM, not the model, names them: `nn` by order from 01, `MSnn_<Name>` with the name's
     characters outside [A-Za-z0-9] turned into `_` (and a leading `MSn_` the model added dropped).
     An `agent_ref` the snapshot doesn't hold becomes null, with a warning in `validation_errors`.
  6. All of them are stored PENDING in one transaction, created by SYSTEM through the state
     machine (`extracted`). The Workflow's state is never touched here: the pipeline moves it.
Failures are non-retryable and leave the reason on `workflows.error_reason` (a code and ids):
`ModuleNotCovered` ("module M-03 not covered") or `SchemaInvalidAfterRetries` once the re-asks are
spent, `LLM_UNAVAILABLE` when Bifrost is down on the last attempt, `ContextTooLarge`,
`LlmRequestRejected`. The pipeline then records EXTRACTION_FAILED and notifies.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final

from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill
from sbam_api.isatvon.repository import MacroSkillRepository
from sbam_api.isatvon.service import require_approved_sow
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.masters.repository import MasterEntryRepository
from sbam_api.projects.models import Project
from sbam_api.state_machine import apply_create
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.isatvon14 import AGENT_REF
from sbam_contracts.isatvon_macros import MAX_MACROS, MacroExtraction
from sbam_contracts.masters import MasterStatus, MasterType
from sbam_contracts.masters_api import SnapshotEntry
from sbam_contracts.pipeline import IdsResult, StageRef, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow import SowDocument
from sbam_contracts.state_machines import Actor
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.llm.client import (
    ContextTooLarge,
    LlmClient,
    LlmRequestRejected,
    LlmTransportError,
)
from sbam_workers.llm.embed import embed
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings
from sbam_workers.sow.context import RESERVED_TOKENS, build_context
from sbam_workers.sow.generate import SowDeps, _bundle

log = logging.getLogger("sbam_workers.isatvon.macros")

ALIAS: Final = "sbam-isatvon"
PROMPT_ID: Final = "isatvon-macros"
EXTRACT_ATTEMPTS: Final = 3  # ProjectPipeline's PLAN uses the same (a test checks)
NAME_CHARS: Final = 100  # of the name part of `MSnn_<Name>`
SCHEMA_INVALID: Final = "SchemaInvalidAfterRetries"
MODULE_NOT_COVERED: Final = "ModuleNotCovered"
SOW_NOT_APPROVED: Final = "SowNotApproved"
TOO_MANY_MODULES: Final = "TooManyModules"
LLM_UNAVAILABLE: Final = "LLM_UNAVAILABLE"
_NOT_COVERED = re.compile(r"^modules\.(\S+): MODULE_NOT_COVERED$")
_PREFIX = re.compile(r"^\s*MS\d{1,2}[._\s-]+", re.IGNORECASE)
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
_SAFE_REF = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class ExtractionError(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


@dataclass(frozen=True)
class Source:
    """What pass 1 reads, loaded in one session."""

    workflow_id: uuid.UUID
    sow_version_id: uuid.UUID
    knowledge_version_id: uuid.UUID
    sow: SowDocument
    agents: dict[str, str]  # AG-* -> "<name> - <description>"
    only: list[str] = field(default_factory=list)  # E7.2: just these modules (none: all)
    stored: list[uuid.UUID] = field(default_factory=list)  # Macro Skills already there, in order
    first_order: int = 1  # of the first new Macro Skill


# --- checks and SBAM's own fields -----------------------------------------------------------------


def check_coverage(
    sow: SowDocument, only: list[str] | None = None
) -> Callable[[MacroExtraction], tuple[str, ...]]:
    """Every module (of `only`, if given) in some `sow_refs`; every ref a module or feature id of
    the SOW (of those modules, if given: the others already have Macro Skills)."""
    modules = list(only) if only else [m.id for m in sow.modules]
    known = set(modules) | {f.id for f in sow.features if not only or f.module_id in only}

    def check(answer: MacroExtraction) -> tuple[str, ...]:
        errors: list[str] = []
        covered: set[str] = set()
        for i, macro in enumerate(answer.macros):
            for j, ref in enumerate(macro.sow_refs):
                if ref in known:
                    covered.add(ref)
                else:
                    shown = ref if _SAFE_REF.match(ref) else "<id>"
                    errors.append(f"macros.{i}.sow_refs.{j}: UNKNOWN_SOW_REF {shown}")
        errors += [f"modules.{m}: MODULE_NOT_COVERED" for m in modules if m not in covered]
        return tuple(dict.fromkeys(errors))

    return check


def macro_category(order: int, name: str) -> str:
    """`MSnn_<Name>`: nn from the order, the name's other characters turned into `_`."""
    words = _NON_WORD.sub("_", _PREFIX.sub("", name)).strip("_")[:NAME_CHARS].rstrip("_")
    return f"MS{order:02d}_{words or 'Macro'}"


def _agent_ref(ref: str | None, agents: dict[str, str]) -> tuple[str | None, list[str]]:
    if ref is None:
        return None, []
    if AGENT_REF.match(ref) and ref in agents:
        return ref, []
    shown = ref if _SAFE_REF.match(ref) else "<id>"
    return None, [f"agent_ref: UNKNOWN_AGENT {shown} (not in the master snapshot; set to null)"]


def _uncovered(attempts: SchemaInvalidAfterRetries) -> list[str]:
    """The modules the last answer left uncovered, if that is all that was wrong with it."""
    last = attempts.attempts[-1].errors if attempts.attempts else ()
    found = [m.group(1) for e in last if (m := _NOT_COVERED.match(e))]
    return found if found and len(found) == len(last) else []


# --- the step -------------------------------------------------------------------------------------


def _ctx(inp: WorkflowRef) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _agents(session: Session, ctx: TenantContext, kv: KnowledgeVersion | None) -> dict[str, str]:
    if kv is None or kv.master_snapshot_id is None:
        return {}
    repo = MasterEntryRepository(session, ctx)
    snapshot = repo.snapshot(kv.master_snapshot_id)
    if snapshot is None:
        return {}
    project = session.get(Project, ctx.project_id)
    allow_restricted = bool(project and project.allow_restricted)
    entries = [SnapshotEntry.model_validate(e) for e in snapshot.entries]
    offered = [
        e
        for e in entries
        if e.master_type is MasterType.AGENTS
        and e.status not in (MasterStatus.ILLUSTRATIVE, MasterStatus.RETIRED)
        and (allow_restricted or not e.restricted)
        and AGENT_REF.match(e.stable_id)
    ]
    versions = repo.versions_at([(e.entry_id, e.version) for e in offered])
    agents: dict[str, str] = {}
    for e in offered:
        pinned = versions.get(e.entry_id)
        payload: dict[str, Any] = dict(pinned.payload) if pinned is not None else {}
        name = str(payload.get("agent_name") or "").strip()
        description = str(payload.get("description") or "").strip()
        agents[e.stable_id] = " - ".join(p for p in (name, description) if p)
    return agents


def _load(deps: SowDeps, inp: WorkflowRef) -> Source | list[uuid.UUID]:
    """The stored Macro Skills' ids, or what pass 1 reads (raising when it may not run)."""
    ctx = _ctx(inp)
    with deps.sessions() as session:
        workflow: Workflow | None = WorkflowRepository(session, ctx).get(inp.workflow_id)
        if workflow is None:
            raise ApplicationError("no such Workflow", type="WorkflowNotFound", non_retryable=True)
        stored = MacroSkillRepository(session, ctx).of_workflow(workflow.id)
        try:
            version = require_approved_sow(session, ctx, workflow)
        except ApiError:
            if stored:
                return [m.id for m in stored]
            raise ExtractionError(SOW_NOT_APPROVED, "SOW_NOT_APPROVED") from None
        sow = SowDocument.model_validate(version.structured)
        only = _uncovered_modules(sow, stored) if stored else []
        if stored and not only:
            return [m.id for m in stored]
        if len(sow.modules) > MAX_MACROS:
            raise ExtractionError(TOO_MANY_MODULES, f"TOO_MANY_MODULES {len(sow.modules)}")
        kv = session.get(KnowledgeVersion, version.knowledge_version_id)
        return Source(
            workflow.id,
            version.id,
            version.knowledge_version_id,
            sow,
            _agents(session, ctx, kv),
            only=only,
            stored=[m.id for m in stored],
            first_order=max((m.order for m in stored), default=0) + 1,
        )


def _uncovered_modules(sow: SowDocument, stored: list[MacroSkill]) -> list[str]:
    """SOW modules no stored Macro Skill covers (by module id, or by one of its features)."""
    module_of = {f.id: f.module_id for f in sow.features}
    covered: set[str] = set()
    for macro in stored:
        for ref in macro.sow_refs:
            covered.add(module_of.get(ref, ref))
    return [m.id for m in sow.modules if m.id not in covered]


def _store(
    deps: SowDeps, inp: WorkflowRef, src: Source, answer: MacroExtraction
) -> list[uuid.UUID]:
    ctx = _ctx(inp)
    rows: list[MacroSkill] = []
    with deps.sessions() as session:
        for order, proposal in enumerate(answer.macros, start=src.first_order):
            agent_ref, warnings = _agent_ref(proposal.agent_ref, src.agents)
            row = MacroSkill(
                id=uuid7(),
                workflow_id=src.workflow_id,
                sow_version_id=src.sow_version_id,
                macro_category=macro_category(order, proposal.name),
                name=proposal.name,
                description=proposal.description,
                order=order,
                sow_refs=list(dict.fromkeys(proposal.sow_refs)),
                agent_ref=agent_ref,
                validation_errors=warnings,
                created_by="SYSTEM",
            )
            apply_create(
                session,
                ctx,
                row,
                event="extracted",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=inp.correlation_id,
                idempotency_key=f"{inp.run_id}:extract_macro_skills:{order}",
            )
            rows.append(row)
        workflow = WorkflowRepository(session, ctx).get(src.workflow_id)
        if workflow is not None and workflow.error_reason is not None:
            workflow.error_reason = None  # this extraction succeeded
        session.commit()
        return [r.id for r in rows]


def _failed(deps: SowDeps, inp: WorkflowRef, kind: str, reason: str) -> ApplicationError:
    with deps.sessions() as session:
        workflow = WorkflowRepository(session, _ctx(inp)).get(inp.workflow_id)
        if workflow is not None:
            workflow.error_reason = reason[:300]
            session.commit()
    log.warning(
        "macro extraction failed",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(inp.workflow_id),
            "type": kind,
            "reason": reason,
        },
    )
    return ApplicationError(reason, type=kind, non_retryable=True)


def _blocks(src: Source, blocks: dict[str, str]) -> dict[str, str]:
    sow = src.sow.model_dump(mode="json", include={"modules", "features"})
    agents = "\n".join(f"{ref}: {text}" for ref, text in sorted(src.agents.items()))
    out = {"sow": json.dumps(sow, sort_keys=True), "agents": agents, **blocks}
    if src.only:  # E7.2: only these modules are broken down now
        out["scope"] = "\n".join(src.only)
    return out


async def extract_macros(deps: SowDeps, inp: WorkflowRef, scope: LlmScope) -> list[uuid.UUID]:
    """The Workflow's Macro Skill ids; see the module docstring."""
    try:
        loaded = await asyncio.to_thread(_load, deps, inp)
    except ExtractionError as exc:
        raise await asyncio.to_thread(_failed, deps, inp, exc.kind, exc.reason) from None
    if isinstance(loaded, list):
        return loaded
    src = loaded

    ref = StageRef(**inp.model_dump(exclude={"workflow_id"}), ref_id=src.knowledge_version_id)
    bundle = await asyncio.to_thread(_bundle, deps, ref)
    queries = {m.id: f"{m.name}. {m.purpose}" for m in src.sow.modules}
    context = await build_context(
        bundle, deps.embed, budget_tokens=deps.budget_tokens, queries=queries
    )
    try:
        result = await deps.llm.run(
            ALIAS,
            PROMPT_ID,
            MacroExtraction,
            _blocks(src, context.blocks),
            scope,
            validate=check_coverage(src.sow, src.only),
            manifest_extra=context.manifest,
        )
    except SchemaInvalidAfterRetries as exc:
        missing = _uncovered(exc)
        if missing:
            reason = f"module {', '.join(missing)} not covered"
            raise await asyncio.to_thread(_failed, deps, inp, MODULE_NOT_COVERED, reason) from None
        raise await asyncio.to_thread(
            _failed, deps, inp, SCHEMA_INVALID, f"SCHEMA_INVALID after {len(exc.attempts)}"
        ) from None
    except (ContextTooLarge, LlmRequestRejected) as exc:
        kind = type(exc).__name__
        raise await asyncio.to_thread(_failed, deps, inp, kind, kind) from None
    except LlmTransportError:
        if scope.attempt < deps.attempts:
            raise
        reason = LLM_UNAVAILABLE
        raise await asyncio.to_thread(_failed, deps, inp, LLM_UNAVAILABLE, reason) from None

    ids = src.stored + await asyncio.to_thread(_store, deps, inp, src, result.output)
    log.info(
        "macro skills extracted",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(inp.workflow_id),
            "macros": len(ids),
            "modules": len(src.sow.modules),
            "mode": context.mode,
            "llm_attempts": result.attempts,
        },
    )
    return ids


# --- the activity ---------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _deps() -> SowDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values from the env
    sessions = mirror.sessions()

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        return await embed(settings, texts)

    return SowDeps(
        sessions=sessions,
        storage=make_backend(settings),
        llm=LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings)),
        embed=embed_texts,
        budget_tokens=settings.llm_max_context_tokens - RESERVED_TOKENS,
        attempts=EXTRACT_ATTEMPTS,
    )


@activity_for(TaskQueue.LLM)
@activity.defn(name="extract_macro_skills")
async def extract_macro_skills(inp: WorkflowRef) -> IdsResult:
    """The Workflow's Macro Skill ids, in order (pass 1)."""
    info = activity.info()
    scope = LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=info.workflow_id or "",
        activity_id=info.activity_id,
        attempt=info.attempt,
    )
    return IdsResult(ids=await extract_macros(_deps(), inp, scope))
