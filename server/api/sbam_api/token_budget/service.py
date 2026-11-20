"""Per-project caps and the raise-once rule (#776).

Every read and write is scoped by tenant and project. A project of another tenant, or one that
does not exist, is a 404 before anything else is looked at. The meter counts the same way the
worker's budget does (`llm_calls`, failed attempts included), so the screen and the refusal agree.
"""

import uuid
from typing import Any

from pydantic import ValidationError
from sbam_contracts.api import ErrorCode
from sbam_contracts.budget import (
    BUILD_PREFIX,
    KNOWLEDGE_ALIASES,
    BudgetCaps,
    BudgetScope,
    BudgetState,
    ProjectTokenCaps,
    RaiseCap,
    TokenMeter,
)
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, check_if_match, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository
from sbam_api.projects.repository import ProjectRepository
from sbam_api.token_budget.repository import FIELDS, TokenCapsRepository, caps_of

ENTITY = "project_token_caps"
CAP_FIELD = {
    BudgetScope.SUB_SKILL: "sub_skill_tokens",
    BudgetScope.WORKFLOW: "workflow_tokens",
    BudgetScope.KNOWLEDGE: "knowledge_tokens",
}


def _project(session: Session, user: CurrentUser, project_id: uuid.UUID) -> TenantContext:
    """The caller's tenant-and-project context; 404 for another tenant's or an unknown project."""
    if ProjectRepository(session, user.context()).get(project_id) is None:
        raise not_found()
    return user.context(project_id)


def _audit(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    record(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.user_id),
        action=action,
        entity_type=ENTITY,
        entity_id=str(project_id),
        before=before,
        after=after,
        correlation_id=correlation_id_var.get() or "unknown",
    )


def get_caps(session: Session, user: CurrentUser, project_id: uuid.UUID) -> ProjectTokenCaps:
    ctx = _project(session, user, project_id)
    row = TokenCapsRepository(session, ctx).current()
    if row is None:
        return ProjectTokenCaps(caps=BudgetCaps(), row_version=0, is_default=True)
    return ProjectTokenCaps(caps=caps_of(row), row_version=row.row_version, is_default=False)


def put_caps(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    caps: BudgetCaps,
    if_match: str | None,
) -> ProjectTokenCaps:
    """Replace the project's caps. `If-Match` is the ETag read before ("0" while nothing is
    stored); a stale or missing one is refused, so two Admins cannot overwrite each other."""
    ctx = _project(session, user, project_id)
    repo = TokenCapsRepository(session, ctx)
    row = repo.current()
    before = (caps_of(row) if row is not None else BudgetCaps()).model_dump()
    check_if_match(if_match, row.row_version if row is not None else 0)
    if row is None:
        row = repo.create(caps, str(user.user_id))
    else:
        for name in FIELDS:
            setattr(row, name, getattr(caps, name))
        row = repo.update(row, row.row_version)
    _audit(session, user, project_id, "TOKEN_CAPS_CHANGED", before, caps.model_dump())
    session.commit()
    session.refresh(row)
    return ProjectTokenCaps(caps=caps_of(row), row_version=row.row_version, is_default=False)


def _used(calls: LlmCallRepository, scope: BudgetScope, build_id: str | None) -> int:
    if scope is BudgetScope.KNOWLEDGE:
        return calls.tokens(KNOWLEDGE_ALIASES, inside=True)
    if scope is BudgetScope.WORKFLOW:
        return calls.tokens(KNOWLEDGE_ALIASES, inside=False)
    return calls.tokens_for_workflow(build_id) if build_id else 0


def raise_key(scope: BudgetScope, build_id: str) -> str:
    """What `raised_builds` holds: one entry per scope of a build. A build stopped by its
    Sub-Skill cap and by the Workflow cap needs both raised, each once."""
    return f"{scope.value}:{build_id}"


def _state(
    caps: BudgetCaps, scope: BudgetScope, used: int, build_id: str | None, raised: list[Any]
) -> BudgetState:
    return BudgetState(
        scope=scope,
        used=used,
        cap=caps.cap(scope),
        level=caps.level(scope, used),
        raised_once=raise_key(scope, build_id) in raised if build_id else False,
        build_id=build_id,
    )


def meter(session: Session, user: CurrentUser, project_id: uuid.UUID) -> TokenMeter:
    ctx = _project(session, user, project_id)
    row = TokenCapsRepository(session, ctx).current()
    caps = BudgetCaps() if row is None else caps_of(row)
    raised: list[Any] = [] if row is None else list(row.raised_builds or [])
    calls = LlmCallRepository(session, ctx)
    heaviest = calls.heaviest_build(BUILD_PREFIX)
    sub_build, sub_used = heaviest if heaviest is not None else (None, 0)
    scopes = [
        _state(caps, BudgetScope.SUB_SKILL, sub_used, sub_build, raised),
        _state(
            caps,
            BudgetScope.WORKFLOW,
            _used(calls, BudgetScope.WORKFLOW, None),
            calls.last_workflow(KNOWLEDGE_ALIASES, inside=False),
            raised,
        ),
        _state(
            caps,
            BudgetScope.KNOWLEDGE,
            _used(calls, BudgetScope.KNOWLEDGE, None),
            calls.last_workflow(KNOWLEDGE_ALIASES, inside=True),
            raised,
        ),
    ]
    return TokenMeter(
        scopes=scopes, first_alert_pct=caps.first_alert_pct, second_alert_pct=caps.second_alert_pct
    )


def _refuse(status: int, detail: str, code: ErrorCode = ErrorCode.CONFLICT) -> ApiError:
    return ApiError(status, code, "Token cap", detail)


def raise_cap(
    session: Session, user: CurrentUser, project_id: uuid.UUID, body: RaiseCap
) -> BudgetState:
    """Raise one scope's cap for one build, once. The FDE or an Admin calls it, then presses
    Retry: the build resumes from its saved step under the new cap. A second raise for the same
    build is refused and audited. The cap must be reached, and the new one must be higher."""
    ctx = _project(session, user, project_id)
    calls = LlmCallRepository(session, ctx)
    if not calls.has_workflow(body.build_id):
        raise not_found()  # unknown, or another project's build
    invalid = ErrorCode.VALIDATION_ERROR
    if body.scope is BudgetScope.SUB_SKILL and not body.build_id.startswith(BUILD_PREFIX):
        raise _refuse(422, "A Sub-Skill cap is raised for a Sub-Skill build.", invalid)
    repo = TokenCapsRepository(session, ctx)
    row = repo.current()
    caps = BudgetCaps() if row is None else caps_of(row)
    raised: list[Any] = [] if row is None else list(row.raised_builds or [])
    field = CAP_FIELD[body.scope]
    old = caps.cap(body.scope)
    key = raise_key(body.scope, body.build_id)
    if key in raised:
        _audit(
            session,
            user,
            project_id,
            "TOKEN_CAP_RAISE_REFUSED",
            {"scope": body.scope.value, "cap": old, "build_id": body.build_id},
            {"requested": body.tokens, "reason": "already raised for this build"},
        )
        session.commit()
        raise _refuse(409, "This build's cap was raised once already; it cannot be raised again.")
    used = _used(calls, body.scope, body.build_id)
    if used < old:
        raise _refuse(409, "This build has not reached its cap; there is nothing to raise.")
    if body.tokens <= old:
        raise _refuse(422, "The new cap must be higher than the current one.", invalid)
    try:
        new_caps = BudgetCaps(**{**caps.model_dump(), field: body.tokens})
    except ValidationError:
        raise _refuse(
            422,
            "A Sub-Skill cap cannot be above the Workflow cap; raise the Workflow cap first.",
            invalid,
        ) from None
    if row is None:
        row = repo.create(new_caps, str(user.user_id))
        row.raised_builds = [key]
    else:
        setattr(row, field, body.tokens)
        row.raised_builds = [*raised, key]
        row = repo.update(row, row.row_version)
    _audit(
        session,
        user,
        project_id,
        "TOKEN_CAP_RAISED",
        {"scope": body.scope.value, "cap": old, "build_id": body.build_id},
        {"scope": body.scope.value, "cap": body.tokens, "build_id": body.build_id},
    )
    session.commit()
    return _state(new_caps, body.scope, used, body.build_id, [*raised, key])
