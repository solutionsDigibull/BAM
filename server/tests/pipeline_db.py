"""Test helpers for E10.1: SQLite with the run mirror, audit/outbox and stand-in project + Workflow
tables registered for the pipeline (the real tables arrive with #54 and #91)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sbam_api.audit.models import AuditLog
from sbam_api.state_machine.registry import register_entity, unregister_entity
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.state_machines import MachineName
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from state_db import SQLITE_MODELS, add, sqlite_sessions

PROJECT_ROW = SQLITE_MODELS[MachineName.PROJECT_PIPELINE]
WORKFLOW_ROW = SQLITE_MODELS[MachineName.WORKFLOW]


def pipeline_sessions() -> sessionmaker[Session]:
    sessions = sqlite_sessions()
    engine = sessions.kw["bind"]
    TemporalRun.metadata.create_all(engine, tables=[TemporalRun.__table__])  # type: ignore[list-item]
    return sessions


@contextmanager
def registered_stand_ins() -> Iterator[None]:
    register_entity(MachineName.PROJECT_PIPELINE, PROJECT_ROW)
    register_entity(MachineName.WORKFLOW, WORKFLOW_ROW)
    try:
        yield
    finally:
        unregister_entity(MachineName.PROJECT_PIPELINE)
        unregister_entity(MachineName.WORKFLOW)


def new_project(
    sessions: sessionmaker[Session], tenant: uuid.UUID, state: str = "IDLE"
) -> uuid.UUID:
    project = uuid.uuid4()
    add(sessions, PROJECT_ROW, state, tenant, project, id=project)
    return project


def state_of(sessions: sessionmaker[Session], model: type[Any], entity_id: uuid.UUID) -> str | None:
    with sessions() as session:
        row = session.get(model, entity_id)
        return None if row is None else str(row.state)


def workflow_rows(sessions: sessionmaker[Session], project: uuid.UUID) -> list[Any]:
    with sessions() as session:
        return list(session.scalars(select(WORKFLOW_ROW).where(WORKFLOW_ROW.project_id == project)))


def actions(sessions: sessionmaker[Session], entity_type: str, entity_id: uuid.UUID) -> list[str]:
    with sessions() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == str(entity_id))
            .order_by(AuditLog.id)
        )
        return [r.action for r in rows]


def mirror_row(sessions: sessionmaker[Session], project: uuid.UUID) -> TemporalRun:
    with sessions() as session:
        row = session.scalars(select(TemporalRun).where(TemporalRun.project_id == project)).one()
        return row
