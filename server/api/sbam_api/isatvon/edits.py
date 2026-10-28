"""The FDE's edits of the ISATVON breakdown (TD §8.8, E8.4): Sub-Skill content and position, Macro
Skill name / description / position, deleting a Sub-Skill, and the per-field edit history.

Rules shared by every write here:
* The Workflow must be in ISATVON_REVIEW (or EXTRACTION_FAILED, to fix what extraction left):
  BUILDING is 409 `WORKFLOW_BUILDING`, FROZEN 423 `WORKFLOW_FROZEN`, any other state 409.
* A Sub-Skill's ETag is its `content_sha256`; a Macro Skill's its `row_version` (428 / 412).
* A content edit moves the Sub-Skill VALID | INVALID → DRAFT → VALID | INVALID through the
  state-machine service (audited), after the whole row went through the TD §10.4 validator:
  a rule violation is stored as INVALID with its findings; a type error is 422 and stores nothing.
* Each changed field is a `sub_skill_edits` row, and the edit one `ISATVON_EDIT` audit record.
* Reordering renumbers the siblings 1..n; `macro_category` and `skill_id` never change on a rename
  or a move (they stay stable references), and a deleted row gives up its `order` for good.
"""

import copy
import json
import uuid
from typing import Any

from pydantic import ValidationError
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, validate_sub_skill
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.isatvon_workbench import (
    PLATFORM_OWNED,
    READ_ONLY_ROW_FIELDS,
    MacroSkillPatch,
    SubSkillEditHistory,
    SubSkillPatch,
)
from sbam_contracts.isatvon_workbench import (
    SubSkillEdit as SubSkillEditOut,
)
from sbam_contracts.state_machines import Actor, MachineName, SubSkillState, WorkflowState, machine
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, check_if_match, locked, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill, SubSkillEdit
from sbam_api.isatvon.repository import (
    MacroSkillRepository,
    SubSkillEditRepository,
    SubSkillRepository,
)
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

MAX_PATCH_BYTES = 256 * 1024
_TEMP_ORDER = 100_000
EDITABLE_WORKFLOW = frozenset({WorkflowState.ISATVON_REVIEW, WorkflowState.EXTRACTION_FAILED})
SUB_SKILL_LOCKED = machine(MachineName.SUB_SKILL).locked  # CERTIFIED, FROZEN, SUPERSEDED
_EDITABLE_SUB = frozenset({SubSkillState.DRAFT, SubSkillState.VALID, SubSkillState.INVALID})
_PREFIXES = {"isatvon_definition": "isatvon_definition", "isatvon": "isatvon_definition"}
_KEEP_CONTROL = {"\n", "\t"}


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _unprocessable(errors: list[FieldError]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid request", errors=errors)


def _read_only(path: str) -> ApiError:
    return ApiError(
        422,
        ErrorCode.READ_ONLY,
        "Read-only field",
        errors=[FieldError(path=path, message="This field is set by the platform, not edited")],
    )


def editable_workflow(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Workflow:
    workflow: Workflow | None = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    state = WorkflowState(workflow.state)
    if state is WorkflowState.FROZEN:
        raise ApiError(
            423, ErrorCode.WORKFLOW_FROZEN, "Workflow frozen", "Create a new version to change it."
        )
    if state is WorkflowState.BUILDING:
        raise ApiError(
            409, ErrorCode.WORKFLOW_BUILDING, "Workflow building", "Wait for the build to finish."
        )
    if state not in EDITABLE_WORKFLOW:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "Not editable now",
            f"The breakdown can be edited in {WorkflowState.ISATVON_REVIEW.value}.",
        )
    return workflow


def _clean(value: Any) -> Any:
    """Strings without control characters (newline and tab stay), recursively."""
    if isinstance(value, str):
        return "".join(ch for ch in value if ch.isprintable() or ch in _KEEP_CONTROL)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _parts(key: str) -> list[str]:
    head, _, rest = key.partition(".")
    if head not in _PREFIXES or not rest:
        raise _unprocessable(
            [FieldError(path=key, message="Only isatvon_definition.* paths can be edited")]
        )
    return [_PREFIXES[head], *rest.split(".")]


def _walk(node: Any, parts: list[str]) -> tuple[Any, str | int]:
    """The container holding the last part, and that part as a dict key or list index."""
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    return node, int(last) if isinstance(node, list) else last


def _get(raw: dict[str, Any], parts: list[str]) -> Any:
    try:
        holder, key = _walk(raw, parts)
        return holder[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _set(raw: dict[str, Any], key: str, parts: list[str], value: Any) -> None:
    try:
        holder, leaf = _walk(raw, parts)
        if isinstance(holder, list):
            holder[leaf] = value  # type: ignore[index]
        elif isinstance(holder, dict):
            holder[leaf] = value
        else:
            raise TypeError
    except (KeyError, IndexError, ValueError, TypeError):
        raise _unprocessable([FieldError(path=key, message="No such field")]) from None


def _field_errors(exc: ValidationError) -> list[FieldError]:
    return [
        FieldError(path=".".join(str(p) for p in err["loc"]), message=err["msg"])
        for err in exc.errors()
    ]


def _check_body(patch: SubSkillPatch) -> dict[str, Any]:
    for key in patch.model_extra or {}:
        raise (
            _read_only(key)
            if key in READ_ONLY_ROW_FIELDS
            else _unprocessable([FieldError(path=key, message="Unknown field")])
        )
    for key in patch.changes:
        if key in PLATFORM_OWNED:
            raise _read_only(key)
    if len(json.dumps(patch.model_dump(mode="json"), default=str)) > MAX_PATCH_BYTES:
        raise ApiError(413, ErrorCode.PAYLOAD_TOO_LARGE, "Edit too large")
    return {key: _clean(value) for key, value in patch.changes.items()}


def _renumber(session: Session, rows: list[Any]) -> None:
    """`order` 1..n in the given sequence. Two passes: the (parent, order) unique constraint is not
    deferrable, so the rows first move out of the way."""
    if all(row.order == i for i, row in enumerate(rows, start=1)):
        return
    for i, row in enumerate(rows, start=1):
        row.order = _TEMP_ORDER + i  # above any real order; deleted rows are negative
    session.flush()
    for i, row in enumerate(rows, start=1):
        row.order = i
    session.flush()


def _move(rows: list[Any], row: Any, order: int) -> list[Any]:
    rest = [r for r in rows if r.id != row.id]
    rest.insert(min(order, len(rest) + 1) - 1, row)
    return rest


def edit_sub_skill(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    patch: SubSkillPatch,
    if_match: str | None,
) -> SubSkill:
    ctx = user.context(project_id)
    editable_workflow(session, ctx, workflow_id)
    repo = SubSkillRepository(session, ctx)
    sub: SubSkill | None = repo.get_for_update(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    state = SubSkillState(sub.state)
    if sub.state in SUB_SKILL_LOCKED:
        raise locked()
    if state not in _EDITABLE_SUB:
        raise ApiError(
            409, ErrorCode.CONFLICT, "Not editable now", f"The Sub-Skill is {sub.state}."
        )
    _check_etag(if_match, sub.content_sha256)
    changes = _check_body(patch)

    candidate = copy.deepcopy(sub.isatvon)
    diffs: list[tuple[str, Any, Any]] = []
    for key, value in changes.items():
        parts = _parts(key)
        old = _get(candidate, parts)
        if old != value:
            _set(candidate, key, parts, value)
            diffs.append((key, old, value))
    if diffs:
        _store_content(session, user, ctx, sub, candidate, diffs)
    if patch.order is not None and patch.order != sub.order:
        _reorder_sub(session, user, ctx, sub, patch.order)
    return sub


def _check_etag(if_match: str | None, sha: str) -> None:
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    if if_match.strip().removeprefix("W/").strip('"') != sha:
        raise ApiError(
            412, ErrorCode.PRECONDITION_FAILED, "Resource changed since you loaded it; reload"
        )


def _store_content(
    session: Session,
    user: CurrentUser,
    ctx: TenantContext,
    sub: SubSkill,
    candidate: dict[str, Any],
    diffs: list[tuple[str, Any, Any]],
) -> None:
    try:
        parsed = SubSkillModel.model_validate(candidate)
    except ValidationError as exc:
        raise _unprocessable(_field_errors(exc)) from None  # a type error: nothing stored
    macro: MacroSkill | None = MacroSkillRepository(session, ctx).get(sub.macro_skill_id)
    findings = validate_sub_skill(
        candidate,
        macro_category=macro.macro_category if macro else None,
        library_registry=DEFAULT_LIBRARY_REGISTRY,
    )
    sub.isatvon = candidate
    sub.skill_name = parsed.skill_metadata.skill_name
    sub.validation_errors = [f.model_dump(mode="json") for f in findings]
    sub.content_sha256 = sha256_of(candidate).removeprefix("sha256:")
    actor_id = str(user.user_id)

    def move(event: str, actor: Actor) -> None:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=sub.id,
                event=event,
                actor=actor,
                actor_id=actor_id,
                correlation_id=_correlation(),
            ),
        )

    if SubSkillState(sub.state) is not SubSkillState.DRAFT:
        move("edit", Actor.FDE)
    move("validation_failed" if findings else "validation_passed", Actor.SYSTEM)
    _log_edits(session, ctx, sub, actor_id, diffs)


def _log_edits(
    session: Session,
    ctx: TenantContext,
    sub: SubSkill,
    actor_id: str,
    diffs: list[tuple[str, Any, Any]],
) -> None:
    for path, old, new in diffs:
        session.add(
            SubSkillEdit(
                tenant_id=ctx.tenant_id,
                project_id=sub.project_id,
                sub_skill_id=sub.id,
                workflow_id=sub.workflow_id,
                path=path,
                old=old,
                new=new,
                edited_by=actor_id,
            )
        )
    session.flush()
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=actor_id,
        action="ISATVON_EDIT",
        entity_type=MachineName.SUB_SKILL.value,
        entity_id=str(sub.id),
        before={path: old for path, old, _ in diffs},
        after={path: new for path, _, new in diffs},
        correlation_id=_correlation(),
    )


def _reorder_sub(
    session: Session, user: CurrentUser, ctx: TenantContext, sub: SubSkill, order: int
) -> None:
    before = sub.order
    siblings = SubSkillRepository(session, ctx).of_macro(sub.macro_skill_id)
    _renumber(session, _move(siblings, sub, order))
    _log_edits(session, ctx, sub, str(user.user_id), [("order", before, sub.order)])


def delete_sub_skill(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    if_match: str | None,
) -> None:
    """Soft delete. The last Sub-Skill of a Macro Skill stays (409 MACRO_REQUIRES_SUB_SKILL), and
    so does one that is certified or frozen (409). `If-Match`, when sent, must match."""
    ctx = user.context(project_id)
    editable_workflow(session, ctx, workflow_id)
    repo = SubSkillRepository(session, ctx)
    sub: SubSkill | None = repo.get_for_update(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    if if_match:
        _check_etag(if_match, sub.content_sha256)
    if sub.state in SUB_SKILL_LOCKED:
        raise ApiError(
            409, ErrorCode.CONFLICT, "Cannot delete", f"A {sub.state} Sub-Skill is kept as it is."
        )
    siblings = repo.of_macro(sub.macro_skill_id)
    if len(siblings) <= 1:
        raise ApiError(
            409,
            ErrorCode.MACRO_REQUIRES_SUB_SKILL,
            "Macro Skill needs a Sub-Skill",
            "Delete the Macro Skill's content by regenerating it, not by emptying it.",
        )
    lowest = repo.lowest_order(sub.macro_skill_id)
    repo.soft_delete(sub)
    sub.order = min(0, lowest) - 1  # frees its place; never reused
    session.flush()
    _renumber(session, [s for s in siblings if s.id != sub.id])
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action="ISATVON_DELETE",
        entity_type=MachineName.SUB_SKILL.value,
        entity_id=str(sub.id),
        before={"skill_id": sub.skill_id, "state": sub.state},
        correlation_id=_correlation(),
    )


def edit_macro_skill(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    macro_id: uuid.UUID,
    patch: MacroSkillPatch,
    if_match: str | None,
) -> MacroSkill:
    ctx = user.context(project_id)
    editable_workflow(session, ctx, workflow_id)
    repo = MacroSkillRepository(session, ctx)
    macro: MacroSkill | None = repo.get_for_update(macro_id)
    if macro is None or macro.workflow_id != workflow_id:
        raise not_found()
    check_if_match(if_match, macro.row_version)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field in ("name", "description"):
        value = getattr(patch, field)
        if value is not None and _clean(value) != getattr(macro, field):
            before[field], after[field] = getattr(macro, field), _clean(value)
            setattr(macro, field, after[field])
    if patch.order is not None and patch.order != macro.order:
        before["order"], after["order"] = macro.order, patch.order
        _renumber(session, _move(repo.of_workflow(workflow_id), macro, patch.order))
        after["order"] = macro.order
    if after:
        repo.update(macro, macro.row_version)
        record(
            session,
            tenant_id=ctx.tenant_id,
            actor_id=str(user.user_id),
            action="ISATVON_EDIT",
            entity_type=MachineName.AGENT.value,
            entity_id=str(macro.id),
            before=before,
            after=after,
            correlation_id=_correlation(),
        )
    return macro


def edit_history(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
) -> SubSkillEditHistory:
    """Oldest first. Readable in every Workflow state; a deleted Sub-Skill is 404."""
    sub: SubSkill | None = SubSkillRepository(session, ctx).get(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    rows = SubSkillEditRepository(session, ctx).of_sub_skill(sub.id)
    return SubSkillEditHistory(
        sub_skill_id=sub.id,
        edits=[
            SubSkillEditOut(
                id=r.id, path=r.path, old=r.old, new=r.new, by=r.edited_by, at=r.created_at
            )
            for r in rows
        ],
    )
