"""`temporal_runs`: the mirror of Temporal executions that the UI polls (TD §7, §13.1, E1.7).

One row per execution (`temporal_workflow_id` + `temporal_run_id`). The API inserts it right after
a successful start; workers update `state` / `current_stage` / `last_error`; the reconciler repairs
rows whose last update was lost. Terminal states are final.
"""

import uuid

from sbam_contracts.runs import RunState, SubjectType
from sqlalchemy import String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check


class TemporalRun(ProjectOwned, Base):
    __tablename__ = "temporal_runs"
    __table_args__ = (
        UniqueConstraint("temporal_workflow_id", "temporal_run_id"),
        status_check(RunState, column="state"),
        status_check(SubjectType, column="subject_type"),
    )

    temporal_workflow_id: Mapped[str] = mapped_column(String(200), index=True)
    temporal_run_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(100))
    task_queue: Mapped[str] = mapped_column(String(32))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(16), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(500))
