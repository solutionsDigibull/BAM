"""Run mirror updates (E1.7): the `update_run_mirror` activity and the reconciler.

Workflows call `update_run_mirror` on every stage change and at the end. It is idempotent and keyed
by the Temporal execution. If the API hasn't written the row yet (a fast workflow can get here
first) it raises a retryable error, so Temporal retries it with backoff.

`reconcile` repairs rows left RUNNING when an update was lost (e.g. the DB was down when the
workflow finished): it asks Temporal for the real status of each stale RUNNING row.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.temporal.repository import TemporalRunRepository, unfinished_runs
from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

STATUS = {
    WorkflowExecutionStatus.COMPLETED: RunState.COMPLETED,
    WorkflowExecutionStatus.FAILED: RunState.FAILED,
    WorkflowExecutionStatus.CANCELED: RunState.CANCELLED,
    WorkflowExecutionStatus.TERMINATED: RunState.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT: RunState.TIMED_OUT,
}

_sessions: sessionmaker[Session] | None = None


def configure(sessions: sessionmaker[Session]) -> None:
    """Use these sessions (tests, bootstrap). Otherwise built from DATABASE_URL on first use."""
    global _sessions
    _sessions = sessions


def sessions() -> sessionmaker[Session]:
    global _sessions
    if _sessions is None:
        _sessions = make_sessionmaker(make_engine(WorkerSettings().database_url))  # type: ignore[call-arg]
    return _sessions


def _apply(update: MirrorUpdate) -> bool:
    with sessions()() as session:
        repo = TemporalRunRepository(session, TenantContext(update.tenant_id, update.project_id))
        done = repo.apply(
            update.temporal_workflow_id,
            update.temporal_run_id,
            update.state,
            update.current_stage,
            update.last_error,
        )
        session.commit()
        return done


@activity_for(*TaskQueue)
@activity.defn(name="update_run_mirror")
def update_run_mirror(update: MirrorUpdate) -> None:
    if not _apply(update):
        raise ApplicationError("mirror row not written yet", type="MirrorRowMissing")


async def reconcile(client: Client, older_than: timedelta = timedelta(minutes=1)) -> int:
    """Fix RUNNING rows whose execution already ended. Returns how many rows changed."""
    cutoff = datetime.now(UTC) - older_than
    rows = await asyncio.to_thread(_stale_rows, cutoff)
    fixed = 0
    for tenant_id, project_id, workflow_id, run_id, stage in rows:
        error: str | None = None
        try:
            status = (
                await client.get_workflow_handle(workflow_id, run_id=run_id).describe()
            ).status
            state = STATUS.get(status) if status is not None else None
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                continue  # Temporal unreachable: try again next round
            state, error = RunState.FAILED, "execution not found in Temporal"
        if state is None:  # still running
            continue
        if state is not RunState.COMPLETED and error is None:
            error = f"run ended {state.value.lower()} (repaired by reconciler)"
        update = MirrorUpdate(
            tenant_id=tenant_id,
            project_id=project_id,
            temporal_workflow_id=workflow_id,
            temporal_run_id=run_id,
            state=state,
            current_stage=stage,
            last_error=error,
        )
        if await asyncio.to_thread(_apply, update):
            fixed += 1
    return fixed


def _stale_rows(cutoff: datetime) -> list[tuple[UUID, UUID, str, str, str | None]]:
    with sessions()() as session:
        return [
            (r.tenant_id, r.project_id, r.temporal_workflow_id, r.temporal_run_id, r.current_stage)
            for r in unfinished_runs(session, cutoff)
        ]
