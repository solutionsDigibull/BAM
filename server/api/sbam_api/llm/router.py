"""`POST /admin/llm-cache/invalidate` (#421 part 5): an admin removes a bad cached model answer.

Tenant-scoped: an admin only ever reaches their own tenant's answers, so another tenant's key
matches nothing and answers 404, the same as an unknown key. By prompt, zero matches is a normal
200 with `invalidated: 0` (nothing was cached for it). One `audit_log` row per call with the
selector and the count, never the cached content. The next identical request is asked again.
"""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sbam_contracts.llm_cache import LlmCacheInvalidate, LlmCacheInvalidated
from sbam_contracts.llm_usage import LlmUsage, LlmUsageStage
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser, admin, fde
from sbam_api.core.conventions import not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.session import app_sessions
from sbam_api.llm.repository import LlmCallRepository, LlmDerivativeRepository
from sbam_api.projects.guard import active_project

router = APIRouter(tags=["llm-cache"])

Admin = Annotated[CurrentUser, Depends(admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


@router.post("/admin/llm-cache/invalidate")
def invalidate(body: LlmCacheInvalidate, user: Admin, session: Db) -> LlmCacheInvalidated:
    ctx = user.context()  # 404 for an account with no tenant
    count = LlmDerivativeRepository(session, ctx).invalidate(
        cache_key=body.cache_key, prompt_id=body.prompt_id, prompt_version=body.prompt_version
    )
    if body.cache_key is not None and count == 0:
        raise not_found()  # unknown, or another tenant's
    record(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.user_id),
        action="LLM_DERIVATIVE_INVALIDATED",
        entity_type="llm_derivative",
        entity_id=body.cache_key,
        after={
            "cache_key": body.cache_key,
            "prompt_id": body.prompt_id,
            "prompt_version": body.prompt_version,
            "invalidated": count,
        },
        correlation_id=correlation_id_var.get() or "unknown",
    )
    session.commit()
    return LlmCacheInvalidated(invalidated=count)


def _money(value: Decimal) -> str:
    return f"{value:.4f}"


@router.get("/projects/{project_id}/llm-usage")
def llm_usage(
    project_id: Annotated[uuid.UUID, Depends(active_project)],
    user: Annotated[CurrentUser, Depends(fde)],
    session: Db,
) -> LlmUsage:
    """What the project has spent on model calls, by stage. Counts, tokens and money only."""
    rows = LlmCallRepository(session, user.context(project_id)).usage_by_alias()
    return LlmUsage(
        total_usd=_money(sum((r[5] for r in rows), Decimal(0))),
        calls=sum(r[1] for r in rows),
        tokens_in=sum(r[3] for r in rows),
        tokens_out=sum(r[4] for r in rows),
        stages=[
            LlmUsageStage(
                alias=a, calls=n, failed=bad, tokens_in=tin, tokens_out=tout, cost_usd=_money(c)
            )
            for a, n, bad, tin, tout, c in rows
        ],
    )
