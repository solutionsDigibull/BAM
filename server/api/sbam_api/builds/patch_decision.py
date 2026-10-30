"""The FDE decides an ISATVON patch the corrector proposed (#499, follows #446).

A correction that changes what a Sub-Skill means is held: the Sub-Skill waits in `NEEDS_FDE_REVIEW`
and the correction's `isatvon_corrected` audit row holds the patch (RFC 6902 on
`/isatvon_definition`), the approved definition's hash and the patched Sub-Skill. This module reads
that row and applies the FDE's decision. The workflow is not touched: after the decision the API
sends the existing `retry`, and the new cycle starts from whatever `sub_skills.isatvon` is.

* The patch waiting for a decision is the newest of the Sub-Skill's `isatvon_corrected`,
  `ISATVON_PATCH_ACCEPTED` and `ISATVON_PATCH_REJECTED` audit rows, if it is a BEHAVIOUR patch and
  the Sub-Skill is still `NEEDS_FDE_REVIEW`. A decision, or a later correction, hides it.
* Accept applies the patched definition to the Sub-Skill. It is stored only if the definition is
  still the one the correction started from (else 409: it changed since), the stored patched
  definition matches its recorded hash, it parses and it passes the TD §10.4 validator (else 422,
  nothing stored). Each changed field is a `sub_skill_edits` row, the change is one `ISATVON_EDIT`
  audit record, and `ISATVON_PATCH_ACCEPTED` names the corrector as the origin. The state stays
  `NEEDS_FDE_REVIEW`: `retry` is what moves it.
* Reject writes `ISATVON_PATCH_REJECTED` (the paths, the reason) and changes nothing else, so the
  retry starts from the approved definition and the rejection is on the record.
* A decision carries the request's `Idempotency-Key` on its audit row, so a repeat of the same
  request finds it and applies nothing twice.
"""

import copy
import uuid
from typing import Any, Final

from pydantic import ValidationError
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.canonical import sha256_of
from sbam_contracts.held_patch import HeldPatch, HeldPatchOp
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, validate_sub_skill
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy.orm import Session

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history, find_by_key, record
from sbam_api.builds.service import NotInReview
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.edits import _check_etag, _get, _log_edits, _parts
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository

CORRECTED: Final = "isatvon_corrected"  # written by the correct stage (`correct.AUDIT_ACTION`)
ACCEPTED: Final = "ISATVON_PATCH_ACCEPTED"
REJECTED: Final = "ISATVON_PATCH_REJECTED"
CORRECTOR: Final = "sbam-corrector"
ENTITY: Final = "sub_skill"
BEHAVIOUR: Final = "BEHAVIOUR"
_RELEVANT: Final = frozenset({CORRECTED, ACCEPTED, REJECTED})


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _sub_skill(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> SubSkill:
    sub: SubSkill | None = SubSkillRepository(session, ctx).get_for_update(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    return sub


def _newest(session: Session, ctx: TenantContext, sub: SubSkill) -> AuditLog | None:
    rows = entity_history(session, ctx.tenant_id, ENTITY, str(sub.id))
    return next((r for r in reversed(rows) if r.action in _RELEVANT), None)


def waiting_row(session: Session, ctx: TenantContext, sub: SubSkill) -> AuditLog | None:
    """The correction row holding the patch, when it still waits for a decision."""
    if sub.state != SubSkillState.NEEDS_FDE_REVIEW:
        return None
    row = _newest(session, ctx, sub)
    if row is None or row.action != CORRECTED or row.after is None:
        return None
    return row if row.after.get("patch_class") == BEHAVIOUR else None


def held_out(sub: SubSkill, row: AuditLog) -> HeldPatch:
    after: dict[str, Any] = row.after or {}
    before: dict[str, Any] = row.before or {}
    return HeldPatch(
        sub_skill_id=sub.id,
        content_sha256=sub.content_sha256,
        attempt=int(after.get("attempt", 1)),
        failed_gate=str(after.get("failed_gate", ""))[:100],
        rationale=str(after.get("rationale", ""))[:4_000],
        changed_paths=[str(p) for p in after.get("changed_paths", [])],
        ops=[HeldPatchOp.model_validate(op) for op in after.get("patch", [])],
        isatvon_sha256_before=str(before.get("isatvon_sha256", "")),
        isatvon_sha256_after=str(after.get("isatvon_sha256", "")),
    )


def get_held(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> HeldPatch | None:
    sub: SubSkill | None = SubSkillRepository(session, ctx).get(sub_id)
    if sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    row = waiting_row(session, ctx, sub)
    return None if row is None else held_out(sub, row)


def _definition(sub_skill: dict[str, Any]) -> Any:
    return sub_skill.get("isatvon_definition")


def _diffs(
    sub: SubSkill, candidate: dict[str, Any], paths: list[str]
) -> list[tuple[str, Any, Any]]:
    diffs: list[tuple[str, Any, Any]] = []
    for path in paths:
        key = ".".join(part for part in path.split("/") if part)
        try:
            old, new = _get(sub.isatvon, _parts(key)), _get(candidate, _parts(key))
        except (KeyError, IndexError, TypeError, ValueError):
            continue  # the path was added or removed: the whole-definition hash still covers it
        if old != new:
            diffs.append((key, old, new))
    return diffs


def _unprocessable(errors: list[FieldError]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid patched ISATVON", errors=errors)


def _decision_exists(
    session: Session, ctx: TenantContext, sub: SubSkill, key: str
) -> AuditLog | None:
    return find_by_key(session, ctx.tenant_id, ENTITY, str(sub.id), key)


def decided(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    idempotency_key: str,
) -> bool:
    """True when this request key already recorded a decision (a repeat of the same request)."""
    ctx = user.context(project_id)
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    return _decision_exists(session, ctx, sub, idempotency_key) is not None


def accept(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    if_match: str | None,
    idempotency_key: str,
    reason: str | None,
) -> bool:
    """Apply the held patch. Returns False when this key already applied it (nothing is written)."""
    ctx = user.context(project_id)
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    if _decision_exists(session, ctx, sub, idempotency_key) is not None:
        return False
    row = waiting_row(session, ctx, sub)
    if row is None or row.before is None or row.after is None:
        raise NotInReview()
    _check_etag(if_match, sub.content_sha256)
    candidate: dict[str, Any] = copy.deepcopy(row.after["sub_skill"])
    started_from = _definition(row.before["sub_skill"])
    if sha256_of(started_from) != sha256_of(_definition(sub.isatvon)):
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "The ISATVON changed since the correction",
            "The held patch was made for another version of this Sub-Skill; retry instead.",
        )
    if sha256_of(_definition(candidate)) != row.after.get("isatvon_sha256"):
        raise ApiError(
            409, ErrorCode.CONFLICT, "The held patch does not match its record", "Retry instead."
        )
    try:
        parsed = SubSkillModel.model_validate(candidate)
    except ValidationError as exc:
        errors = [
            FieldError(path="/" + "/".join(str(p) for p in e["loc"]), message=e["msg"])
            for e in exc.errors()
        ]
        raise _unprocessable(errors) from None
    macro: MacroSkill | None = MacroSkillRepository(session, ctx).get(sub.macro_skill_id)
    findings = validate_sub_skill(
        candidate,
        macro_category=macro.macro_category if macro else None,
        library_registry=DEFAULT_LIBRARY_REGISTRY,
    )
    if findings:
        raise _unprocessable([FieldError(path=f.path, message=f.message) for f in findings])
    changed = [str(p) for p in row.after.get("changed_paths", [])]
    diffs = _diffs(sub, candidate, changed)
    sub.isatvon = candidate
    sub.skill_name = parsed.skill_metadata.skill_name
    sub.validation_errors = []
    sub.content_sha256 = sha256_of(candidate).removeprefix("sha256:")
    sub.validated_at = utcnow()
    actor = str(user.user_id)
    if diffs:
        _log_edits(session, ctx, sub, actor, diffs)
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=actor,
        action=ACCEPTED,
        entity_type=ENTITY,
        entity_id=str(sub.id),
        before={"isatvon_sha256": row.after.get("isatvon_sha256")},
        after={
            "origin": CORRECTOR,
            "changed_paths": changed,
            "attempt": row.after.get("attempt"),
            "reason": reason,
        },
        correlation_id=_correlation(),
        idempotency_key=idempotency_key,
    )
    return True


def reject(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    idempotency_key: str,
    reason: str | None,
) -> bool:
    """Record the rejection. Returns False when this key already recorded it."""
    ctx = user.context(project_id)
    sub = _sub_skill(session, ctx, workflow_id, sub_id)
    if _decision_exists(session, ctx, sub, idempotency_key) is not None:
        return False
    row = waiting_row(session, ctx, sub)
    if row is None or row.after is None:
        raise NotInReview()
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=REJECTED,
        entity_type=ENTITY,
        entity_id=str(sub.id),
        after={
            "origin": CORRECTOR,
            "changed_paths": [str(p) for p in row.after.get("changed_paths", [])],
            "attempt": row.after.get("attempt"),
            "reason": reason,
        },
        correlation_id=_correlation(),
        idempotency_key=idempotency_key,
    )
    return True
