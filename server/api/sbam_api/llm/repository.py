"""Tenant- and project-scoped `llm_calls` access (E6.1). Rows are only ever added: a call is
history."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantScopedRepository
from sbam_api.llm.models import LlmCall, LlmCallStatus, LlmDerivative


class LlmCallRepository(TenantScopedRepository[LlmCall]):
    model = LlmCall

    def insert(self, values: dict[str, Any]) -> LlmCall:
        """The tenant and project always come from the context, never from `values`."""
        row = LlmCall(**{**values, "tenant_id": self.ctx.tenant_id})
        if self.ctx.project_id is not None:
            row.project_id = self.ctx.project_id
        self.session.add(row)
        self.session.flush()
        return row

    def cached(
        self, temporal_workflow_id: str, activity_id: str, activity_attempt: int, prompt_id: str
    ) -> LlmCall | None:
        """The SUCCEEDED row of this activity attempt, if a replay can reuse it."""
        query = (
            self._scoped()
            .where(
                LlmCall.temporal_workflow_id == temporal_workflow_id,
                LlmCall.activity_id == activity_id,
                LlmCall.activity_attempt == activity_attempt,
                LlmCall.prompt_id == prompt_id,
                LlmCall.status == LlmCallStatus.SUCCEEDED,
                LlmCall.output.is_not(None),
            )
            .order_by(LlmCall.id.desc())
            .limit(1)
        )
        return self.session.scalars(query).one_or_none()

    def for_activity(self, temporal_workflow_id: str, activity_id: str) -> list[LlmCall]:
        query = (
            self._scoped()
            .where(
                LlmCall.temporal_workflow_id == temporal_workflow_id,
                LlmCall.activity_id == activity_id,
            )
            .order_by(LlmCall.id)
        )
        return list(self.session.scalars(query))

    def usage_by_alias(self) -> list[tuple[str, int, int, int, int, Decimal]]:
        """(alias, calls, failed, tokens_in, tokens_out, cost) per model alias in scope, dearest
        first. Same scope and the same billed-attempts rule as `spent`."""
        cost = func.coalesce(func.sum(LlmCall.cost_usd), 0)
        query = (
            select(
                LlmCall.alias,
                func.count(),
                func.count().filter(LlmCall.status != "SUCCEEDED"),
                func.coalesce(func.sum(LlmCall.tokens_in), 0),
                func.coalesce(func.sum(LlmCall.tokens_out), 0),
                cost,
            )
            .where(LlmCall.tenant_id == self.ctx.tenant_id, LlmCall.deleted_at.is_(None))
            .group_by(LlmCall.alias)
            .order_by(cost.desc(), LlmCall.alias)
        )
        if self.ctx.project_id is not None:
            query = query.where(LlmCall.project_id == self.ctx.project_id)
        return [
            (str(a), int(n), int(bad), int(tin), int(tout), Decimal(str(c)))
            for a, n, bad, tin, tout, c in self.session.execute(query)
        ]

    def spent(self) -> Decimal:
        """Total `cost_usd` of every attempt in scope: the project's when the context names one,
        else the whole tenant's. Failed attempts count: they were billed."""
        query = select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.tenant_id == self.ctx.tenant_id, LlmCall.deleted_at.is_(None)
        )
        if self.ctx.project_id is not None:
            query = query.where(LlmCall.project_id == self.ctx.project_id)
        return Decimal(str(self.session.scalar(query) or 0))


class LlmDerivativeRepository(TenantScopedRepository[LlmDerivative]):
    model = LlmDerivative

    def by_key(self, cache_key: str) -> LlmDerivative | None:
        query = self._scoped().where(LlmDerivative.cache_key == cache_key)
        return self.session.scalars(query).one_or_none()

    def invalidate(
        self, *, cache_key: str | None, prompt_id: str | None, prompt_version: str | None
    ) -> int:
        """Delete this tenant's matching answers; how many went. The next identical request is
        asked again. A cross-tenant key matches nothing (the query is tenant-scoped)."""
        query = delete(LlmDerivative).where(LlmDerivative.tenant_id == self.ctx.tenant_id)
        if cache_key is not None:
            query = query.where(LlmDerivative.cache_key == cache_key)
        if prompt_id is not None:
            query = query.where(LlmDerivative.prompt_id == prompt_id)
        if prompt_version is not None:
            query = query.where(LlmDerivative.prompt_version == prompt_version)
        result = self.session.execute(query.execution_options(synchronize_session=False))
        return int(getattr(result, "rowcount", 0) or 0)

    def hit(self, row: LlmDerivative) -> None:
        """Count a hit with an atomic UPDATE: concurrent hits must not trip the row-version
        check."""
        self.session.execute(
            update(LlmDerivative)
            .where(LlmDerivative.id == row.id)
            .values(hits=LlmDerivative.hits + 1, last_hit_at=utcnow())
            .execution_options(synchronize_session=False)
        )

    def put(self, values: dict[str, Any], *, replace: bool = False) -> bool:
        """Store an answer. An entry that is deleted, or `replace`, is overwritten; otherwise the
        first writer wins and this returns False."""
        key = values["cache_key"]
        existing = self.session.scalars(
            select(LlmDerivative).where(
                LlmDerivative.tenant_id == self.ctx.tenant_id, LlmDerivative.cache_key == key
            )
        ).one_or_none()
        if existing is not None:
            if existing.deleted_at is None and not replace:
                return False
            for name, value in values.items():
                setattr(existing, name, value)
            existing.deleted_at = None
            self.session.flush()
            return True
        row = LlmDerivative(**{**values, "tenant_id": self.ctx.tenant_id})
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            return False
        return True


def delete_unused_derivatives(session: Session, cutoff: datetime, limit: int) -> int:
    """System job only (#497): hard-delete up to `limit` cached answers whose last use is before
    `cutoff`, in every tenant; the number removed. Last use is `last_hit_at`, else the row's
    `created_at`, so a hit keeps an answer alive. Soft-deleted rows count too: they still hold the
    text. The caller commits."""
    last_use = func.coalesce(LlmDerivative.last_hit_at, LlmDerivative.created_at)
    ids = select(LlmDerivative.id).where(last_use < cutoff).order_by(last_use).limit(limit)
    result = session.execute(
        delete(LlmDerivative)
        .where(LlmDerivative.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
