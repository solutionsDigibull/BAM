"""Queries on `temporal_runs`. Feature code uses `TemporalRunRepository` (tenant-scoped).

`unfinished_runs` is the one cross-tenant query: the reconciler (a system job with no caller) uses
it to find rows still RUNNING. It is never reachable from a request.
"""

import uuid
from datetime import UTC, datetime

from sbam_contracts.runs import TERMINAL_STATES, Run, RunState, SubjectType
from sqlalchemy import select
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.temporal.models import TemporalRun


class TemporalRunRepository(TenantScopedRepository[TemporalRun]):
    model = TemporalRun

    def find(self, temporal_workflow_id: str, temporal_run_id: str) -> TemporalRun | None:
        query = self._scoped().where(
            TemporalRun.temporal_workflow_id == temporal_workflow_id,
            TemporalRun.temporal_run_id == temporal_run_id,
        )
        return self.session.scalars(query).one_or_none()

    def latest_for_subject(self, subject_type: str, subject_id: uuid.UUID) -> TemporalRun | None:
        """The newest run recorded for a subject (a document version, a project, ...)."""
        query = (
            self._scoped()
            .where(TemporalRun.subject_type == subject_type, TemporalRun.subject_id == subject_id)
            .order_by(TemporalRun.id.desc())
            .limit(1)
        )
        return self.session.scalars(query).one_or_none()

    def apply(
        self,
        temporal_workflow_id: str,
        temporal_run_id: str,
        state: RunState,
        current_stage: str | None,
        last_error: str | None,
    ) -> bool:
        """Record a state/stage change. Idempotent; a terminal row never changes again.

        Returns False only when there is no row yet (the API writes it right after the start, so
        a fast workflow can get here first; the caller retries).
        """
        row = self.find(temporal_workflow_id, temporal_run_id)
        if row is None:
            return False
        if RunState(row.state) in TERMINAL_STATES:
            return True
        if (row.state, row.current_stage, row.last_error) == (state, current_stage, last_error):
            return True
        row.state, row.current_stage, row.last_error = state.value, current_stage, last_error
        self.update(row, row.row_version)
        return True


def unfinished_runs(
    session: Session, started_before: datetime, limit: int = 100
) -> list[TemporalRun]:
    """System job only (reconciler): RUNNING rows of every tenant, oldest first."""
    query = (
        select(TemporalRun)
        .where(
            TemporalRun.state == RunState.RUNNING.value,
            TemporalRun.deleted_at.is_(None),
            TemporalRun.created_at < started_before,
        )
        .order_by(TemporalRun.id)
        .limit(limit)
    )
    return list(session.scalars(query))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)  # SQLite drops the zone


def to_run(row: TemporalRun) -> Run:
    return Run(
        id=row.id,
        temporal_workflow_id=row.temporal_workflow_id,
        temporal_run_id=row.temporal_run_id,
        type=row.type,
        subject_type=SubjectType(row.subject_type),
        subject_id=row.subject_id,
        state=RunState(row.state),
        current_stage=row.current_stage,
        last_error=row.last_error,
        started_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )
