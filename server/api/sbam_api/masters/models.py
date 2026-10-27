"""Masters engine tables (TD §7.3, E3.2).

* `master_entries`: one row per master row of a tenant (optionally bound to one project). It carries
  the SBAM metadata (`stable_id`, `status`, `source_kind`) and points at its current version.
* `master_entry_versions`: the payloads, append-only. A change is a new version n+1.
* `master_snapshots`: the versions a project used, pinned with their hash, append-only.
* `master_documents`: context files an Admin attaches to a master (E3.8), routed by Filetype.

The append-only guard is a trigger on both tables. Postgres gets it from the migration; the DDL
hooks below add the same guard wherever the tables are created with `create_all` (the SQLite test
databases), so the rule is tested without Docker too.
"""

import uuid
from datetime import datetime

from sbam_contracts.documents import ScanStatus
from sbam_contracts.masters import MasterStatus, MasterType, SourceKind
from sbam_contracts.masters_api import ParseStatus, RouteSource
from sqlalchemy import (
    JSON,
    Connection,
    DateTime,
    ForeignKey,
    Index,
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
from sbam_api.db.base import SCHEMA, Base, TenantOwned, status_check, utcnow

Json = JSON().with_variant(JSONB(), "postgresql")


class MasterEntry(TenantOwned, Base):
    __tablename__ = "master_entries"
    __table_args__ = (
        status_check(MasterStatus),
        status_check(MasterType, "master_type"),
        status_check(SourceKind, "source_kind"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey(f"{SCHEMA}.projects.id"), index=True
    )
    master_type: Mapped[str] = mapped_column(String(20))
    stable_id: Mapped[str] = mapped_column(String(64))
    row_label: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    source_kind: Mapped[str] = mapped_column(String(20))
    current_version: Mapped[int] = mapped_column(Integer, default=1)


# One stable id per type within the tenant, and separately within each project (NULLs are distinct
# in a plain unique index, hence the two partial ones).
_TENANT_SCOPE = MasterEntry.project_id.is_(None)
_PROJECT_SCOPE = MasterEntry.project_id.is_not(None)
Index(
    "uq_master_entries_tenant_stable_id",
    MasterEntry.tenant_id,
    MasterEntry.master_type,
    MasterEntry.stable_id,
    unique=True,
    postgresql_where=_TENANT_SCOPE,
    sqlite_where=_TENANT_SCOPE,
)
Index(
    "uq_master_entries_project_stable_id",
    MasterEntry.tenant_id,
    MasterEntry.project_id,
    MasterEntry.master_type,
    MasterEntry.stable_id,
    unique=True,
    postgresql_where=_PROJECT_SCOPE,
    sqlite_where=_PROJECT_SCOPE,
)


class MasterEntryVersion(Base):
    __tablename__ = "master_entry_versions"
    __table_args__ = (UniqueConstraint("entry_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey(f"{SCHEMA}.master_entries.id"))
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))


class MasterSnapshot(Base):
    __tablename__ = "master_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{SCHEMA}.projects.id"), index=True
    )
    entries: Mapped[list[dict[str, object]]] = mapped_column(Json)
    # E3.8b: the parsed master documents pinned with the entries (`SnapshotDocument`s).
    documents: Mapped[list[dict[str, object]]] = mapped_column(
        Json, default=list, server_default="[]"
    )
    sha256: Mapped[str] = mapped_column(String(71))  # "sha256:" + 64 hex
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))


_ROUTED = (MasterType.ORGANIZATION, MasterType.RULES, MasterType.AGENTS, MasterType.SKILLS)


class MasterDocument(TenantOwned, Base):
    """A file attached to a master. `project_id` empty means the whole tenant."""

    __tablename__ = "master_documents"
    __table_args__ = (
        status_check([m.value for m in _ROUTED], "master_type"),
        status_check(RouteSource, "route_source"),
        status_check(ScanStatus, "scan_status"),  # NULL (not scanned yet) passes a CHECK
        status_check(ParseStatus, "parse_status"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey(f"{SCHEMA}.projects.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    filetype_row_label: Mapped[str] = mapped_column(String(40))
    intent_class: Mapped[str] = mapped_column(String(100))
    master_type: Mapped[str] = mapped_column(String(20))
    master_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey(f"{SCHEMA}.master_entries.id")
    )
    route_source: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    scan_status: Mapped[str | None] = mapped_column(String(20))
    scan_signature_version: Mapped[str | None] = mapped_column(String(100))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_status: Mapped[str] = mapped_column(String(20), default=ParseStatus.PENDING.value)
    # E3.8b: why a FAILED parse failed, the loader's report and the derivatives (own content hash).
    parse_code: Mapped[str | None] = mapped_column(String(40))
    parse_report: Mapped[dict[str, object] | None] = mapped_column(Json)
    derived_md_key: Mapped[str | None] = mapped_column(String(512))  # doc.md
    derived_json_key: Mapped[str | None] = mapped_column(String(512))  # doc.json


# The same bytes are attached once per scope: the tenant's own, and each project's (a plain unique
# index treats NULL project ids as distinct, hence the two partial ones).
_DOC_TENANT = MasterDocument.project_id.is_(None) & MasterDocument.deleted_at.is_(None)
_DOC_PROJECT = MasterDocument.project_id.is_not(None) & MasterDocument.deleted_at.is_(None)
Index(
    "uq_master_documents_tenant_sha256",
    MasterDocument.tenant_id,
    MasterDocument.sha256,
    unique=True,
    postgresql_where=_DOC_TENANT,
    sqlite_where=_DOC_TENANT,
)
Index(
    "uq_master_documents_project_sha256",
    MasterDocument.tenant_id,
    MasterDocument.project_id,
    MasterDocument.sha256,
    unique=True,
    postgresql_where=_DOC_PROJECT,
    sqlite_where=_DOC_PROJECT,
)


# --- Append-only guard (Postgres: migration, see sql.py) -------------------------------------


def _sqlite_guard(target: Table, connection: Connection, **_: object) -> None:
    """The same guard on SQLite (test databases built with `create_all`)."""
    if connection.dialect.name != "sqlite":
        return  # Postgres gets the plpgsql trigger from the migration
    for op in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{target.name}_no_{op.lower()}" BEFORE {op} ON "{target.name}" '
            f"BEGIN SELECT RAISE(ABORT, '{target.name} is append-only ({op})'); END"
        )


for _model in (MasterEntryVersion, MasterSnapshot):
    event.listen(_model.__table__, "after_create", _sqlite_guard)
