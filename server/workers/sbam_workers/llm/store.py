"""Where `llm_calls` rows are written and read (E6.1). The client talks to `CallStore`; production
uses
`SqlCallStore` over the API's tenant-scoped repository, tests can use the same class on SQLite."""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository
from sqlalchemy.orm import Session, sessionmaker


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
            return row.id
