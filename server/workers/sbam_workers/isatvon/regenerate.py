"""`regenerate_skills` (E8.4, TD §8.8): re-extract one Macro Skill's Sub-Skills, or one Sub-Skill,
with the FDE's optional hint.

The same `sbam-isatvon` pass 2 as `extract_sub_skills`, asked again for the Macro Skill with the
hint, the names now in place and, for one Sub-Skill, the one to redo. What is stored differs from
the first extraction: a Sub-Skill that already exists keeps its `id`, `skill_id` and `order` (they
are stable references) and gets the new content in place, so the previous definition stays in
`sub_skill_edits` (`isatvon_definition`, old and new) and the row goes DRAFT → VALID | INVALID.

* Macro Skill: the answer's k-th Sub-Skill replaces the k-th existing one. More answers than rows
  add rows (a fresh `skill_id`, never one a deleted row still holds); fewer soft-delete the rest.
* One Sub-Skill: the answer at the same position replaces it (`NoMatchingSubSkill` if the answer is
  shorter); the other Sub-Skills are not touched.
A retry after the commit finds the content already in place and changes nothing (same SHA).
Workflow must be ISATVON_REVIEW or EXTRACTION_FAILED and no Sub-Skill certified or frozen
(`WorkflowNotEditable`, `SubSkillLocked`, non-retryable, left on `workflows.error_reason`).
"""

import asyncio
import copy
import json
import logging
import uuid
from typing import Any

from sbam_api.audit.repository import record
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.edits import EDITABLE_WORKFLOW, SUB_SKILL_LOCKED
from sbam_api.isatvon.models import SubSkill, SubSkillEdit
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.state_machine import TransitionCommand, apply_create, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, FRAMEWORK_VERSION, validate_sub_skill
from sbam_contracts.isatvon_macros import SubSkillExtraction
from sbam_contracts.isatvon_workbench import RegenerateInput
from sbam_contracts.pipeline import IdsResult, MacroRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor, MachineName, SubSkillState
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.isatvon.sub_skills import (
    ExtractionError,
    Source,
    _deps,
    _failed,
    _load,
    ask_sub_skills,
    finish,
)
from sbam_workers.llm.store import LlmScope
from sbam_workers.registry import activity_for
from sbam_workers.sow.generate import SowDeps

log = logging.getLogger("sbam_workers.isatvon.regenerate")

NOT_EDITABLE = "WorkflowNotEditable"
SUB_SKILL_IS_LOCKED = "SubSkillLocked"
NO_MATCH = "NoMatchingSubSkill"
PATH = "isatvon_definition"


def _ctx(inp: RegenerateInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _ref(inp: RegenerateInput) -> MacroRef:
    return MacroRef(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        run_id=inp.run_id,
        correlation_id=inp.correlation_id,
        workflow_id=inp.workflow_id,
        macro_skill_id=inp.macro_skill_id,
    )


def _guard(deps: SowDeps, inp: RegenerateInput) -> list[dict[str, str]]:
    """What is in place now (id, name, skill_id, in order), or why it may not be regenerated."""
    ctx = _ctx(inp)
    with deps.sessions() as session:
        workflow: Workflow | None = WorkflowRepository(session, ctx).get(inp.workflow_id)
        if workflow is None or workflow.state not in {s.value for s in EDITABLE_WORKFLOW}:
            raise ExtractionError(NOT_EDITABLE, "WORKFLOW_NOT_EDITABLE")
        rows = SubSkillRepository(session, ctx).of_macro(inp.macro_skill_id)
        if any(r.state in SUB_SKILL_LOCKED for r in rows):
            raise ExtractionError(SUB_SKILL_IS_LOCKED, "SUB_SKILL_LOCKED")
        if inp.sub_skill_id is not None and inp.sub_skill_id not in {r.id for r in rows}:
            raise ExtractionError(NO_MATCH, "NO_MATCHING_SUB_SKILL")
        return [{"id": str(r.id), "skill_id": r.skill_id, "name": r.skill_name} for r in rows]


def _blocks(inp: RegenerateInput, current: list[dict[str, str]]) -> dict[str, str]:
    """The hint is the FDE's own text and goes to the model as a data block, never into a prompt."""
    blocks = {"current": json.dumps(current, sort_keys=True)}
    if inp.hint:
        blocks["hint"] = inp.hint
    if inp.sub_skill_id is not None:
        one = next(c for c in current if c["id"] == str(inp.sub_skill_id))
        blocks["focus"] = json.dumps({"skill_id": one["skill_id"], "name": one["name"]})
    return blocks


def _unique(wanted: str, used: set[str]) -> str:
    chosen, n = wanted, 1
    while chosen in used:
        n += 1
        chosen = f"{wanted}_r{n}"
    used.add(chosen)
    return chosen


def _replace(
    session: Session,
    ctx: TenantContext,
    inp: RegenerateInput,
    row: SubSkill,
    raw: dict[str, Any],
    refs: list[str],
) -> bool:
    """The row's content in place, DRAFT → VALID | INVALID, the old definition in its history."""
    sha = sha256_of(raw).removeprefix("sha256:")
    if sha == row.content_sha256:
        return False  # a retry, or the model answered the same
    findings = validate_sub_skill(
        raw,
        macro_category=raw["skill_metadata"]["macro_category"],
        library_registry=DEFAULT_LIBRARY_REGISTRY,
    )
    old = copy.deepcopy(row.isatvon.get(PATH))
    row.isatvon = raw
    row.skill_name = raw["skill_metadata"]["skill_name"]
    row.sow_refs = refs
    row.validation_errors = [f.model_dump(mode="json") for f in findings]
    row.content_sha256 = sha

    def move(event: str, actor: Actor, actor_id: str) -> None:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=row.id,
                event=event,
                actor=actor,
                actor_id=actor_id,
                correlation_id=inp.correlation_id,
                idempotency_key=f"{inp.run_id}:regenerate:{row.id}:{event}",
            ),
        )

    if SubSkillState(row.state) is not SubSkillState.DRAFT:
        move("edit", Actor.FDE, inp.actor_id)
    move("validation_failed" if findings else "validation_passed", Actor.SYSTEM, SYSTEM_ACTOR_ID)
    session.add(
        SubSkillEdit(
            tenant_id=ctx.tenant_id,
            project_id=row.project_id,
            sub_skill_id=row.id,
            workflow_id=row.workflow_id,
            path=PATH,
            old=old,
            new=copy.deepcopy(raw.get(PATH)),
            edited_by=inp.actor_id,
        )
    )
    session.flush()
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=inp.actor_id,
        action="ISATVON_REGENERATE",
        entity_type=MachineName.SUB_SKILL.value,
        entity_id=str(row.id),
        after={"hint_chars": len(inp.hint or ""), "state": row.state},
        correlation_id=inp.correlation_id,
    )
    return True


def _create(
    session: Session,
    ctx: TenantContext,
    inp: RegenerateInput,
    src: Source,
    raw: dict[str, Any],
    refs: list[str],
    order: int,
) -> SubSkill:
    findings = validate_sub_skill(
        raw, macro_category=src.macro_category, library_registry=DEFAULT_LIBRARY_REGISTRY
    )
    row = SubSkill(
        id=uuid7(),
        macro_skill_id=src.macro_id,
        workflow_id=src.workflow_id,
        skill_id=raw["skill_metadata"]["skill_id"],
        skill_name=raw["skill_metadata"]["skill_name"],
        order=order,
        isatvon=raw,
        isatvon_framework_version=FRAMEWORK_VERSION,
        sow_refs=refs,
        validation_errors=[f.model_dump(mode="json") for f in findings],
        content_sha256=sha256_of(raw).removeprefix("sha256:"),
        created_by=inp.actor_id,
    )
    key = f"{inp.run_id}:regenerate:new:{order}"
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
    return row


def _store(
    deps: SowDeps, inp: RegenerateInput, src: Source, answer: SubSkillExtraction
) -> list[uuid.UUID]:
    ctx = _ctx(inp)
    finished = finish(src, answer)
    refs = [list(dict.fromkeys(p.sow_refs)) for p in answer.sub_skills]
    with deps.sessions() as session:
        repo = SubSkillRepository(session, ctx)
        rows = repo.of_macro(src.macro_id)
        if inp.sub_skill_id is not None:
            at = next(k for k, r in enumerate(rows) if r.id == inp.sub_skill_id)
            if at >= len(finished):
                raise ExtractionError(NO_MATCH, "NO_MATCHING_SUB_SKILL")
            row = rows[at]
            raw = finished[at]
            raw["skill_metadata"]["skill_id"] = row.skill_id
            raw["isatvon_definition"]["n_notification"]["next"] = row.isatvon[PATH][
                "n_notification"
            ].get("next")
            _replace(session, ctx, inp, row, raw, refs[at])
            session.commit()
            return [r.id for r in rows]
        used = repo.used_skill_ids(src.workflow_id)
        ids = [rows[k].skill_id if k < len(rows) else None for k in range(len(finished))]
        for k, raw in enumerate(finished):
            ids[k] = ids[k] or _unique(raw["skill_metadata"]["skill_id"], used)
        out: list[uuid.UUID] = []
        lowest = repo.lowest_order(src.macro_id)
        for k, raw in enumerate(finished):
            raw["skill_metadata"]["skill_id"] = ids[k]
            raw["isatvon_definition"]["n_notification"]["next"] = (
                ids[k + 1] if k + 1 < len(ids) else src.next_macro
            )
            if k < len(rows):
                _replace(session, ctx, inp, rows[k], raw, refs[k])
                out.append(rows[k].id)
            else:
                out.append(
                    _create(
                        session, ctx, inp, src, raw, refs[k], rows[-1].order + k - len(rows) + 1
                    ).id
                )
        for gone in rows[len(finished) :]:
            lowest = min(0, lowest) - 1
            repo.soft_delete(gone)
            gone.order = lowest  # frees its place; never reused
            record(
                session,
                tenant_id=ctx.tenant_id,
                actor_id=inp.actor_id,
                action="ISATVON_DELETE",
                entity_type=MachineName.SUB_SKILL.value,
                entity_id=str(gone.id),
                before={"skill_id": gone.skill_id, "state": gone.state},
                correlation_id=inp.correlation_id,
            )
        session.commit()
        return out


async def regenerate(deps: SowDeps, inp: RegenerateInput, scope: LlmScope) -> list[uuid.UUID]:
    """The Macro Skill's Sub-Skill ids, in order; see the module docstring."""
    ref = _ref(inp)
    try:
        current = await asyncio.to_thread(_guard, deps, inp)
        loaded = await asyncio.to_thread(_load, deps, ref, False)
    except ExtractionError as exc:
        raise await asyncio.to_thread(_failed, deps, ref, exc.kind, exc.reason) from None
    if not isinstance(loaded, Source):  # pragma: no cover - reuse_stored is off
        raise ApplicationError("unexpected", type="Unexpected", non_retryable=True)
    result = await ask_sub_skills(deps, ref, scope, loaded, _blocks(inp, current))
    try:
        ids = await asyncio.to_thread(_store, deps, inp, loaded, result.output)
    except ExtractionError as exc:
        raise await asyncio.to_thread(_failed, deps, ref, exc.kind, exc.reason) from None
    log.info(
        "sub-skills regenerated",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(inp.workflow_id),
            "macro_skill_id": str(inp.macro_skill_id),
            "sub_skills": len(ids),
            "one": inp.sub_skill_id is not None,
        },
    )
    return ids


@activity_for(TaskQueue.LLM)
@activity.defn(name="regenerate_skills")
async def regenerate_skills(inp: RegenerateInput) -> IdsResult:
    """The regenerated Macro Skill's Sub-Skill ids, in order."""
    info = activity.info()
    scope = LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=info.workflow_id or "",
        activity_id=info.activity_id,
        attempt=info.attempt,
    )
    return IdsResult(ids=await regenerate(_deps(), inp, scope))
