"""Test helpers for E1.7: an in-memory `temporal_runs` table and a fake Temporal client.

SQLite has no schema `sbam`, so the engine maps it away (`schema_translate_map`). The database is a
temp file, not `:memory:`, so FastAPI's and Temporal's worker threads each get their own connection.
"""

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sbam_api.temporal.models import TemporalRun
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode


def memory_sessions() -> sessionmaker[Session]:
    fd, path = tempfile.mkstemp(prefix="sbam-runs-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    TemporalRun.metadata.create_all(engine, tables=[TemporalRun.__table__])  # type: ignore[list-item]
    return sessionmaker(engine, expire_on_commit=False)


def all_rows(sessions: sessionmaker[Session]) -> list[TemporalRun]:
    with sessions() as session:
        return list(session.query(TemporalRun).order_by(TemporalRun.id))


@dataclass
class FakeHandle:
    id: str
    result_run_id: str
    first_execution_run_id: str
    status: WorkflowExecutionStatus | None = None
    missing: bool = False

    async def describe(self) -> Any:
        if self.missing:
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
        return type("Description", (), {"status": self.status})()


@dataclass
class FakeClient:
    """Mimics `Client.start_workflow` with USE_EXISTING: one execution per workflow id."""

    down: bool = False
    executions: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    statuses: dict[str, WorkflowExecutionStatus] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)

    async def start_workflow(self, workflow: str, arg: Any, **kwargs: Any) -> FakeHandle:
        self.calls.append({"workflow": workflow, "arg": arg, **kwargs})
        if self.down:
            raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")
        run_id = self.executions.setdefault(kwargs["id"], f"run-{len(self.executions) + 1}")
        return FakeHandle(kwargs["id"], run_id, run_id)

    def get_workflow_handle(self, workflow_id: str, *, run_id: str) -> FakeHandle:
        return FakeHandle(
            workflow_id,
            run_id,
            run_id,
            status=self.statuses.get(workflow_id),
            missing=workflow_id in self.missing,
        )


def age(sessions: sessionmaker[Session], when: datetime) -> None:
    """Make every row look as if it started at `when` (for the reconciler's grace period)."""
    with sessions() as session:
        for row in session.query(TemporalRun):
            row.created_at = when
        session.commit()
