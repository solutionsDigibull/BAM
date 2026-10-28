"""`extract_sub_skills` (E8.3, TD §10.3-10.4, §13.2): ISATVON pass 2, one Macro Skill's Sub-Skills.

The ProjectPipeline runs it once per Macro Skill, in parallel (bounded). `extract_for_macro` is the
whole step and takes its dependencies as arguments (the SOW step's `SowDeps`; no bundle or embedder
is used), so tests run it with a fake Bifrost. For Macro Skill `inp.macro_skill_id`:

  1. Sub-Skills already stored for the Macro Skill are returned as they are (activity retry): one
     set per Macro Skill, never a second one.
  2. The Workflow's SOW version must still be the approved one (`require_approved_sow`).
  3. Context: the Macro Skill, the SOW features its `sow_refs` name (a module id stands for all of
     its features) with the mapping hints of `breakdown_hints`, the next Macro Skill and the
     runtime's library registry.
  4. `sbam-isatvon` answers a `SubSkillExtraction`. Beyond the schema, every feature must be in some
     Sub-Skill's `sow_refs` (`FEATURE_NOT_COVERED`) and every ref must be one of those features
     (`UNKNOWN_SOW_REF`); both are asked again like a schema error (codes and ids only). TD §10.4
     rule findings are never asked again: they are stored.
  5. SBAM, not the model, sets the ids: `skill_id` `MSnn.m_<Name>` (m by order from 1, a repeated
     name gets `_2`, `_3`, ...), `macro_category` = the Macro Skill's, and `next` = the following
     Sub-Skill, the last one pointing at the next Macro Skill (null after the last).
  6. A secret value (`v_variables.secrets`) is replaced by `[REDACTED]` before anything is stored,
     also in the `llm_calls` row; the finding (SECRET_VALUE_NOT_ALLOWED) stays.
  7. Each Sub-Skill is validated (`validate_sub_skill`, the build stage's validator), stored DRAFT
     (`extracted`) and moved to VALID or INVALID with its findings, every field kept.
Failures are non-retryable and leave the reason on `workflows.error_reason` (a code and ids):
`FeatureNotCovered` ("MS02_X: feature F-Y-1 not covered") or `SchemaInvalidAfterRetries` once the
re-asks are spent (the Macro Skill then has no Sub-Skill), `LLM_UNAVAILABLE` on the last attempt,
`SowNotApproved`, `ContextTooLarge`, `LlmRequestRejected`. Other Macro Skills keep their rows; the
pipeline records EXTRACTION_FAILED.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.isatvon.service import require_approved_sow
from sbam_api.llm.models import LlmCall
from sbam_api.state_machine import TransitionCommand, apply_create, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import (
    DEFAULT_LIBRARY_REGISTRY,
    FRAMEWORK_VERSION,
    MACRO_CATEGORY,
    SECRET_NAME,
    SECRET_VALUE,
    validate_sub_skill,
)
from sbam_contracts.isatvon_macros import SubSkillExtraction
from sbam_contracts.pipeline import IdsResult, MacroRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow import FeatureItem, SowDocument, breakdown_hints
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
from sbam_workers.sow.context import RESERVED_TOKENS
from sbam_workers.sow.generate import SowDeps

log = logging.getLogger("sbam_workers.isatvon.sub_skills")

ALIAS: Final = "sbam-isatvon"
PROMPT_ID: Final = "isatvon-sub-skills"
EXTRACT_ATTEMPTS: Final = 3  # ProjectPipeline's PLAN uses the same (a test checks)
NAME_CHARS: Final = 100  # of the name part of `MSnn.m_<Name>`
REDACTED: Final = "[REDACTED]"
SCHEMA_INVALID: Final = "SchemaInvalidAfterRetries"
FEATURE_NOT_COVERED: Final = "FeatureNotCovered"
SOW_NOT_APPROVED: Final = "SowNotApproved"
LLM_UNAVAILABLE: Final = "LLM_UNAVAILABLE"
_NOT_COVERED = re.compile(r"^features\.(\S+): FEATURE_NOT_COVERED$")
_PREFIX = re.compile(r"^\s*MS\d{1,2}[._]\d+[._\s-]+", re.IGNORECASE)
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
_SAFE_REF = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class ExtractionError(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


@dataclass(frozen=True)
class Source:
    """What pass 2 reads for one Macro Skill, loaded in one session."""

    macro_id: uuid.UUID
    workflow_id: uuid.UUID
    macro_category: str
    name: str
    description: str
    next_macro: str | None
    features: list[FeatureItem]
    hints: dict[str, dict[str, Any]]  # feature id -> breakdown hint


# --- checks and SBAM's own fields -----------------------------------------------------------------


def check_coverage(features: list[FeatureItem]) -> Callable[[SubSkillExtraction], tuple[str, ...]]:
    """Every feature in some `sow_refs`; every ref one of the Macro Skill's features."""
    ids = [f.id for f in features]

    def check(answer: SubSkillExtraction) -> tuple[str, ...]:
        errors: list[str] = []
        covered: set[str] = set()
        for i, proposal in enumerate(answer.sub_skills):
            for j, ref in enumerate(proposal.sow_refs):
                if ref in ids:
                    covered.add(ref)
                else:
                    shown = ref if _SAFE_REF.match(ref) else "<id>"
                    errors.append(f"sub_skills.{i}.sow_refs.{j}: UNKNOWN_SOW_REF {shown}")
        errors += [f"features.{f}: FEATURE_NOT_COVERED" for f in ids if f not in covered]
        return tuple(dict.fromkeys(errors))

    return check


def skill_ids(macro_category: str, names: list[str]) -> list[str]:
    """`MSnn.m_<Name>` for each name in order: m from 1, other characters turned into `_`, a
    leading `MSnn.m_` the model wrote dropped, a repeated name suffixed `_2`, `_3`, ..."""
    match = MACRO_CATEGORY.match(macro_category)
    nn = match.group(1) if match else "00"
    seen: dict[str, int] = {}
    out: list[str] = []
    for m, name in enumerate(names, start=1):
        word = _NON_WORD.sub("_", _PREFIX.sub("", name)).strip("_")[:NAME_CHARS].rstrip("_")
        word = word or "Skill"
        seen[word.casefold()] = seen.get(word.casefold(), 0) + 1
        if seen[word.casefold()] > 1:
            word = f"{word}_{seen[word.casefold()]}"
        out.append(f"MS{nn}.{m}_{word}")
    return out


def redact_secrets(raw: dict[str, Any]) -> bool:
    """Replace secret values in `isatvon_definition.v_variables.secrets` in place; True if any."""
    variables = raw.get("isatvon_definition", {}).get("v_variables", {})
    secrets = variables.get("secrets")
    if not isinstance(secrets, list):
        return False
    found = False
    for k, secret in enumerate(secrets):
        if not isinstance(secret, str):
            continue
        if SECRET_VALUE.search(secret) or not SECRET_NAME.match(secret):
            secrets[k] = REDACTED
            found = True
    return found


def finish(src: Source, answer: SubSkillExtraction) -> list[dict[str, Any]]:
    """The model's Sub-Skills with SBAM's ids and `next` chain, secrets redacted (JSON dicts)."""
    names = [p.sub_skill.skill_metadata.skill_name for p in answer.sub_skills]
    ids = skill_ids(src.macro_category, names)
    out: list[dict[str, Any]] = []
    for k, proposal in enumerate(answer.sub_skills):
        raw = proposal.sub_skill.model_dump(mode="json", exclude_unset=True)  # as answered
        raw["skill_metadata"]["skill_id"] = ids[k]
        raw["skill_metadata"]["macro_category"] = src.macro_category
        if names.count(names[k]) > 1:
            raw["skill_metadata"]["skill_name"] = f"{names[k]} ({names[: k + 1].count(names[k])})"
        following = ids[k + 1] if k + 1 < len(ids) else src.next_macro
        raw["isatvon_definition"]["n_notification"]["next"] = following
        redact_secrets(raw)
        out.append(raw)
    return out


def _uncovered(attempts: SchemaInvalidAfterRetries) -> list[str]:
    """The features the last answer left uncovered, if that is all that was wrong with it."""
    last = attempts.attempts[-1].errors if attempts.attempts else ()
    found = [m.group(1) for e in last if (m := _NOT_COVERED.match(e))]
    return found if found and len(found) == len(last) else []


# --- the step -------------------------------------------------------------------------------------


def _ctx(inp: MacroRef) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _features(sow: SowDocument, refs: list[str]) -> list[FeatureItem]:
    """The features a Macro Skill's `sow_refs` name, in SOW order (a module id: all of its)."""
    modules = {m.id for m in sow.modules}
    wanted: set[str] = set()
    for ref in refs:
        wanted |= {f.id for f in sow.features_of(ref)} if ref in modules else {ref}
    return [f for f in sow.features if f.id in wanted]


def _load(deps: SowDeps, inp: MacroRef) -> Source | list[uuid.UUID]:
    """The stored Sub-Skills' ids, or what pass 2 reads (raising when it may not run)."""
    ctx = _ctx(inp)
    with deps.sessions() as session:
        macros = MacroSkillRepository(session, ctx)
        macro: MacroSkill | None = macros.get(inp.macro_skill_id)
        workflow: Workflow | None = WorkflowRepository(session, ctx).get(inp.workflow_id)
        if macro is None or workflow is None or macro.workflow_id != workflow.id:
            raise ApplicationError("no such Macro Skill", type="MacroNotFound", non_retryable=True)
        stored = SubSkillRepository(session, ctx).of_macro(macro.id)
        if stored:
            return [s.id for s in stored]
        try:
            version = require_approved_sow(session, ctx, workflow)
        except ApiError:
            raise ExtractionError(SOW_NOT_APPROVED, "SOW_NOT_APPROVED") from None
        sow = SowDocument.model_validate(version.structured)
        siblings = macros.of_workflow(workflow.id)
        later = [m for m in siblings if m.order > macro.order]
        hints = {h["feature_id"]: h for m in breakdown_hints(sow) for h in m["sub_skills"]}
        return Source(
            macro.id,
            workflow.id,
            macro.macro_category,
            macro.name,
            macro.description,
            later[0].macro_category if later else None,
            _features(sow, list(macro.sow_refs)),
            hints,
        )


def _blocks(src: Source) -> dict[str, str]:
    macro = {
        "macro_category": src.macro_category,
        "name": src.name,
        "description": src.description,
    }
    features = [
        {**f.model_dump(mode="json", exclude={"citations"}), "hints": src.hints.get(f.id, {})}
        for f in src.features
    ]
    return {
        "macro": json.dumps(macro, sort_keys=True),
        "features": json.dumps(features, sort_keys=True),
        "next_macro": src.next_macro or "none",
        "libraries": "\n".join(sorted(DEFAULT_LIBRARY_REGISTRY)),
    }


def _store(
    deps: SowDeps,
    inp: MacroRef,
    src: Source,
    answer: SubSkillExtraction,
    llm_call_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    ctx = _ctx(inp)
    finished = finish(src, answer)
    rows: list[SubSkill] = []
    with deps.sessions() as session:
        for k, (raw, proposal) in enumerate(zip(finished, answer.sub_skills, strict=True)):
            findings = validate_sub_skill(
                raw, macro_category=src.macro_category, library_registry=DEFAULT_LIBRARY_REGISTRY
            )
            row = SubSkill(
                id=uuid7(),
                macro_skill_id=src.macro_id,
                workflow_id=src.workflow_id,
                skill_id=raw["skill_metadata"]["skill_id"],
                skill_name=raw["skill_metadata"]["skill_name"],
                order=k + 1,
                isatvon=raw,
                isatvon_framework_version=FRAMEWORK_VERSION,
                sow_refs=list(dict.fromkeys(proposal.sow_refs)),
                validation_errors=[f.model_dump(mode="json") for f in findings],
                content_sha256=sha256_of(raw).removeprefix("sha256:"),
                created_by="SYSTEM",
            )
            key = f"{inp.run_id}:extract_sub_skills:{src.macro_id}:{k + 1}"
            apply_create(
                session,
                ctx,
                row,
                event="extracted",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=inp.correlation_id,
                idempotency_key=key,
            )
            apply_transition(
                session,
                ctx,
                TransitionCommand(
                    model=SubSkill,
                    entity_id=row.id,
                    event="validation_failed" if findings else "validation_passed",
                    actor=Actor.SYSTEM,
                    actor_id=SYSTEM_ACTOR_ID,
                    correlation_id=inp.correlation_id,
                    idempotency_key=f"{key}:validated",
                ),
            )
            rows.append(row)
        if llm_call_id is not None:
            _redact_call(session, llm_call_id)
        session.commit()
        return [r.id for r in rows]


def _redact_call(session: Session, llm_call_id: uuid.UUID) -> None:
    """The `llm_calls` row keeps the model's answer: no secret value may stay in it either."""
    call = session.get(LlmCall, llm_call_id)
    if call is None or not isinstance(call.output, dict):
        return
    output = json.loads(json.dumps(call.output))
    changed = False
    for proposal in output.get("sub_skills", []):
        if isinstance(proposal, dict) and isinstance(proposal.get("sub_skill"), dict):
            changed |= redact_secrets(proposal["sub_skill"])
    if changed:
        call.output = output


def _failed(deps: SowDeps, inp: MacroRef, kind: str, reason: str) -> ApplicationError:
    with deps.sessions() as session:
        WorkflowRepository(session, _ctx(inp)).set_error_reason(inp.workflow_id, reason)
        session.commit()
    log.warning(
        "sub-skill extraction failed",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(inp.workflow_id),
            "macro_skill_id": str(inp.macro_skill_id),
            "type": kind,
            "reason": reason,
        },
    )
    return ApplicationError(reason, type=kind, non_retryable=True)


async def extract_for_macro(deps: SowDeps, inp: MacroRef, scope: LlmScope) -> list[uuid.UUID]:
    """The Macro Skill's Sub-Skill ids; see the module docstring."""
    try:
        loaded = await asyncio.to_thread(_load, deps, inp)
    except ExtractionError as exc:
        raise await asyncio.to_thread(_failed, deps, inp, exc.kind, exc.reason) from None
    if isinstance(loaded, list):
        return loaded
    src = loaded
    try:
        result = await deps.llm.run(
            ALIAS,
            PROMPT_ID,
            SubSkillExtraction,
            _blocks(src),
            scope,
            validate=check_coverage(src.features),
            subject={"macro_skill_id": str(src.macro_id), "macro_category": src.macro_category},
        )
    except SchemaInvalidAfterRetries as exc:
        missing = _uncovered(exc)
        if missing:
            reason = f"{src.macro_category}: feature {', '.join(missing)} not covered"
            raise await asyncio.to_thread(_failed, deps, inp, FEATURE_NOT_COVERED, reason) from None
        reason = f"{src.macro_category}: SCHEMA_INVALID after {len(exc.attempts)}"
        raise await asyncio.to_thread(_failed, deps, inp, SCHEMA_INVALID, reason) from None
    except (ContextTooLarge, LlmRequestRejected) as exc:
        kind = type(exc).__name__
        reason = f"{src.macro_category}: {kind}"
        raise await asyncio.to_thread(_failed, deps, inp, kind, reason) from None
    except LlmTransportError:
        if scope.attempt < deps.attempts:
            raise
        reason = f"{src.macro_category}: {LLM_UNAVAILABLE}"
        raise await asyncio.to_thread(_failed, deps, inp, LLM_UNAVAILABLE, reason) from None

    ids = await asyncio.to_thread(_store, deps, inp, src, result.output, result.llm_call_id)
    log.info(
        "sub-skills extracted",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(inp.workflow_id),
            "macro_skill_id": str(inp.macro_skill_id),
            "sub_skills": len(ids),
            "features": len(src.features),
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
@activity.defn(name="extract_sub_skills")
async def extract_sub_skills(inp: MacroRef) -> IdsResult:
    """The Macro Skill's Sub-Skill ids, in order (pass 2)."""
    info = activity.info()
    scope = LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=info.workflow_id or "",
        activity_id=info.activity_id,
        attempt=info.attempt,
    )
    return IdsResult(ids=await extract_for_macro(_deps(), inp, scope))
