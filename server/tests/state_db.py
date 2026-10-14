"""Test helpers for E1.8: small `Stateful` entity tables and an SQLite database with the real
`audit_log` / `notification_outbox` tables. Entity tables for real features arrive with their
issues; these stand in for them (one per machine the tests use).
"""

import os
import tempfile
import uuid
from typing import Any

from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.db.base import NAMING_CONVENTION, ProjectOwned
from sbam_api.state_machine import Stateful, state_check
from sbam_contracts.state_machines import MachineName
from sqlalchemy import Boolean, MetaData, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def entity_base(schema: str | None = None) -> type[DeclarativeBase]:
    class _Base(DeclarativeBase):
        metadata = MetaData(schema=schema, naming_convention=NAMING_CONVENTION)

    return _Base


def entity_models(base: Any) -> dict[MachineName, type[Any]]:
    """One table per machine, on `base` (SQLite: no schema; Postgres: a scratch schema)."""

    def model(name: MachineName, table: str, **extra: Any) -> type[Any]:
        attrs: dict[str, Any] = {
            "__tablename__": table,
            "__table_args__": (state_check(name),),
            "machine": name,
            **extra,
        }
        return type(
            f"{name.value.title().replace('_', '')}Row", (Stateful, ProjectOwned, base), attrs
        )

    return {
        MachineName.PROJECT_PIPELINE: model(MachineName.PROJECT_PIPELINE, "t_projects"),
        MachineName.DOCUMENT_VERSION: model(MachineName.DOCUMENT_VERSION, "t_document_versions"),
        MachineName.SOW_VERSION: model(MachineName.SOW_VERSION, "t_sow_versions"),
        MachineName.WORKFLOW: model(MachineName.WORKFLOW, "t_workflows"),
        MachineName.SUB_SKILL: model(
            MachineName.SUB_SKILL,
            "t_sub_skills",
            is_frozen=mapped_column(Boolean, default=False),
            __annotations__={"is_frozen": Mapped[bool]},
        ),
    }


_SQLITE_BASE = entity_base()
SQLITE_MODELS = entity_models(_SQLITE_BASE)


def sqlite_sessions() -> sessionmaker[Session]:
    """Temp-file SQLite (not :memory:): the security-audit write uses its own connection."""
    fd, path = tempfile.mkstemp(prefix="sbam-sm-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    AuditLog.metadata.create_all(
        engine,
        tables=[AuditLog.__table__, NotificationOutbox.__table__],  # type: ignore[list-item]
    )
    _SQLITE_BASE.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def add(
    sessions: sessionmaker[Session],
    model: type[Any],
    state: str,
    tenant: uuid.UUID,
    project: uuid.UUID,
    **fields: Any,
) -> uuid.UUID:
    """Insert an entity directly in `state` (test setup only; real code uses apply_create)."""
    with sessions() as session:
        row = model(tenant_id=tenant, project_id=project, state=state, **fields)
        session.add(row)
        session.commit()
        entity_id: uuid.UUID = row.id
        return entity_id


def audit_rows(
    sessions: sessionmaker[Session], entity_id: uuid.UUID | None = None
) -> list[AuditLog]:
    with sessions() as session:
        query = select(AuditLog).order_by(AuditLog.id)
        if entity_id is not None:
            query = query.where(AuditLog.entity_id == str(entity_id))
        return list(session.scalars(query))


def outbox_rows(sessions: sessionmaker[Session]) -> list[NotificationOutbox]:
    with sessions() as session:
        return list(session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id)))


def current(
    sessions: sessionmaker[Session], model: type[Any], entity_id: uuid.UUID
) -> tuple[str, int]:
    with sessions() as session:
        row = session.get(model, entity_id)
        assert row is not None
        return row.state, row.row_version
