"""`filetype_row_versions` (E3.13, ADR 0020): super-admin versions of the 43 Filetype rows.

Platform data, not a tenant's: one set of Filetype rows for the whole platform, like `tenants`.
A row with no version here is the shipped BAM 0.2 row (`frozen_filetype()`); a super-admin
workbook import appends a version for each row it changes. Append-only, like
`master_entry_versions`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Connection,
    DateTime,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.core.ids import uuid7
from sbam_api.db.base import Base, utcnow

Json = JSON().with_variant(JSONB(), "postgresql")


class FiletypeRowVersion(Base):
    __tablename__ = "filetype_row_versions"
    __table_args__ = (UniqueConstraint("row_label", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    row_label: Mapped[str] = mapped_column(String(32))  # "Filetype #7"
    version: Mapped[int] = mapped_column(Integer)  # 2 is the first change; 1 is the shipped row
    payload: Mapped[dict[str, object]] = mapped_column(Json)
    workbook_sha256: Mapped[str] = mapped_column(String(71))
    version_label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))


def _sqlite_guard(target: Table, connection: Connection, **_: object) -> None:
    """Append-only on SQLite (test databases); Postgres gets the trigger from the migration."""
    if connection.dialect.name != "sqlite":
        return
    for op in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{target.name}_no_{op.lower()}" BEFORE {op} ON "{target.name}" '
            f"BEGIN SELECT RAISE(ABORT, '{target.name} is append-only ({op})'); END"
        )


event.listen(FiletypeRowVersion.__table__, "after_create", _sqlite_guard)
