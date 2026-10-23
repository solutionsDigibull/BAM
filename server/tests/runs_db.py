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
from sbam_contracts.build import SubSkillBuildStatus
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
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

    async def query(self, name: str, **_: Any) -> Any:
        """No pipeline answers queries here: like Temporal for a workflow that never started."""
        raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")


@dataclass
class FakeBuildHandle:
    """A SubSkillBuild child, queried/signalled the way `builds/service.py` expects."""

    id: str
    status: SubSkillBuildStatus
    signals: list[tuple[str, str, Any]]

    async def describe(self, **_: Any) -> None:
        return None

    async def query(self, name: str, **_: Any) -> SubSkillBuildStatus:
        return self.status

    async def signal(self, name: str, payload: Any) -> None:
        self.signals.append((self.id, name, payload))


@dataclass
class FakeClient:
    """Mimics `Client.start_workflow` with USE_EXISTING: one execution per workflow id."""

    down: bool = False
    executions: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    statuses: dict[str, WorkflowExecutionStatus] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    builds: dict[str, SubSkillBuildStatus] = field(default_factory=dict)
    build_signals: list[tuple[str, str, Any]] = field(default_factory=list)

    async def start_workflow(self, workflow: str, arg: Any, **kwargs: Any) -> FakeHandle:
        self.calls.append({"workflow": workflow, "arg": arg, **kwargs})
        if self.down:
            raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")
        policy = kwargs.get("id_conflict_policy")
        if policy == WorkflowIDConflictPolicy.FAIL and kwargs["id"] in self.executions:
            raise WorkflowAlreadyStartedError(kwargs["id"], workflow)
        run_id = self.executions.setdefault(kwargs["id"], f"run-{len(self.executions) + 1}")
        return FakeHandle(kwargs["id"], run_id, run_id)

    def get_workflow_handle(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> FakeHandle | FakeBuildHandle:
        """`run_id` is how run-based callers (pipeline/start) ask; SubSkillBuild callers
        (builds/service.py) never pass it, so that's how a build lookup is told apart."""
        if run_id is None and workflow_id.startswith("pipeline-"):
            # Temporal hands out a handle for any id; the query on it is what fails (E7.1).
            return FakeHandle(workflow_id, "", "", missing=True)
        if run_id is None:
            status = self.builds.get(workflow_id)
            if status is None:
                raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
            return FakeBuildHandle(workflow_id, status, self.build_signals)
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
