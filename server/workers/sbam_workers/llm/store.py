"""Where `llm_calls` rows are written and read (E6.1). The client talks to `CallStore`; production
uses
`SqlCallStore` over the API's tenant-scoped repository, tests can use the same class on SQLite."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository, LlmDerivativeRepository
from sbam_api.notifications.service import Source, notify_project
from sbam_api.token_budget.repository import effective_caps
from sbam_contracts.budget import BudgetCaps
from sbam_contracts.notifications import NotificationType
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.llm.spend_metrics import record as record_cost


@dataclass(frozen=True)
class LlmScope:
    """Who a call is for and which activity attempt makes it (the replay key)."""

    tenant_id: uuid.UUID
    project_id: uuid.UUID
    correlation_id: str
    temporal_workflow_id: str
    activity_id: str
    attempt: int = 1  # the Temporal activity attempt


@dataclass(frozen=True)
class Cached:
    llm_call_id: uuid.UUID
    trace_id: str
    output: dict[str, Any]


class CallStore(Protocol):
    def cached(self, scope: LlmScope, prompt_id: str) -> Cached | None: ...

    def add(self, scope: LlmScope, values: dict[str, Any]) -> uuid.UUID: ...

    def derivative(self, scope: LlmScope, cache_key: str) -> dict[str, Any] | None:
        """The stored answer for `cache_key` in the tenant (counting the hit), or None."""
        ...

    def put_derivative(
        self, scope: LlmScope, values: dict[str, Any], *, replace: bool = False
    ) -> None: ...

    def spent(self, scope: LlmScope, *, tenant: bool = False) -> Decimal:
        """Recorded spend of the scope's project, or of its whole tenant."""
        ...

    def spent_build(self, scope: LlmScope) -> Decimal:
        """Recorded spend of the build workflow in the scope (`temporal_workflow_id`), failed
        attempts included (#926)."""
        ...

    def tokens(self, scope: LlmScope, aliases: frozenset[str], *, inside: bool) -> int:
        """Tokens (in + out) recorded for the scope's project, for these aliases or all others."""
        ...

    def tokens_build(self, scope: LlmScope) -> int:
        """Tokens (in + out) recorded under the build workflow in the scope: one Sub-Skill build's
        own count (#776)."""
        ...

    def caps(self, scope: LlmScope) -> BudgetCaps:
        """The project's token caps: its own row, else the platform defaults (#776)."""
        ...

    def alert(
        self, scope: LlmScope, type: NotificationType, key: str, params: dict[str, object]
    ) -> None:
        """Tell the project's people once per `key` (a repeat writes nothing)."""
        ...


class SqlCallStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def cached(self, scope: LlmScope, prompt_id: str) -> Cached | None:
        with self.sessions() as session:
            row = LlmCallRepository(
                session, TenantContext(scope.tenant_id, scope.project_id)
            ).cached(scope.temporal_workflow_id, scope.activity_id, scope.attempt, prompt_id)
            if row is None or row.output is None:
                return None
            return Cached(row.id, row.trace_id, row.output)

    def add(self, scope: LlmScope, values: dict[str, Any]) -> uuid.UUID:
        with self.sessions() as session:
            row = LlmCallRepository(
                session, TenantContext(scope.tenant_id, scope.project_id)
            ).insert(
                {
                    **values,
                    "correlation_id": scope.correlation_id,
                    "temporal_workflow_id": scope.temporal_workflow_id,
                    "activity_id": scope.activity_id,
                    "activity_attempt": scope.attempt,
                    "created_by": "sbam-worker",
                }
            )
            session.commit()
            record_cost(row.alias, row.cost_usd)
            return row.id

    def derivative(self, scope: LlmScope, cache_key: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            repo = LlmDerivativeRepository(session, TenantContext(scope.tenant_id, None))
            row = repo.by_key(cache_key)
            if row is None:
                return None
            repo.hit(row)
            session.commit()
            return row.output

    def put_derivative(
        self, scope: LlmScope, values: dict[str, Any], *, replace: bool = False
    ) -> None:
        with self.sessions() as session:
            repo = LlmDerivativeRepository(session, TenantContext(scope.tenant_id, None))
            repo.put({**values, "created_by": "sbam-worker"}, replace=replace)
            session.commit()

    def spent(self, scope: LlmScope, *, tenant: bool = False) -> Decimal:
        with self.sessions() as session:
            ctx = TenantContext(scope.tenant_id, None if tenant else scope.project_id)
            return LlmCallRepository(session, ctx).spent()

    def spent_build(self, scope: LlmScope) -> Decimal:
        with self.sessions() as session:
            ctx = TenantContext(scope.tenant_id, scope.project_id)
            return LlmCallRepository(session, ctx).spent_for_workflow(scope.temporal_workflow_id)

    def tokens(self, scope: LlmScope, aliases: frozenset[str], *, inside: bool) -> int:
        with self.sessions() as session:
            ctx = TenantContext(scope.tenant_id, scope.project_id)
            return LlmCallRepository(session, ctx).tokens(aliases, inside=inside)

    def tokens_build(self, scope: LlmScope) -> int:
        with self.sessions() as session:
            ctx = TenantContext(scope.tenant_id, scope.project_id)
            return LlmCallRepository(session, ctx).tokens_for_workflow(scope.temporal_workflow_id)

    def caps(self, scope: LlmScope) -> BudgetCaps:
        with self.sessions() as session:
            return effective_caps(session, scope.tenant_id, scope.project_id)

    def alert(
        self, scope: LlmScope, type: NotificationType, key: str, params: dict[str, object]
    ) -> None:
        with self.sessions() as session:
            notify_project(
                session,
                scope.tenant_id,
                type=type,
                project_id=scope.project_id,
                source=Source("token-budget", key),
                params=params,
            )
            session.commit()
