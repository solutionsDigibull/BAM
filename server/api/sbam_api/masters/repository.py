"""Tenant-scoped queries for the masters engine (E3.2).

`master_entries` goes through `TenantScopedRepository`. Versions and snapshots are append-only
tables without the soft-delete / row-version columns, so their queries filter on the caller's tenant
here explicitly; nothing reads them without a tenant.
"""

import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.masters.models import MasterEntry, MasterEntryVersion, MasterSnapshot

_LABEL_NUMBER = re.compile(r"#(\d+)\s*$")


class MasterEntryRepository(TenantScopedRepository[MasterEntry]):
    model = MasterEntry

    def of_type(self, master_type: str, project_id: uuid.UUID | None = None) -> list[MasterEntry]:
        """Tenant entries of a type plus, with `project_id`, that project's own entries."""
        query = self._scoped().where(MasterEntry.master_type == master_type)
        if project_id is None:
            query = query.where(MasterEntry.project_id.is_(None))
        else:
            query = query.where(
                MasterEntry.project_id.is_(None) | (MasterEntry.project_id == project_id)
            )
        return list(self.session.scalars(query.order_by(MasterEntry.id)))

    def for_project(self, project_id: uuid.UUID) -> list[MasterEntry]:
        """Every live entry a project's snapshot can see: the tenant's and the project's own."""
        query = self._scoped().where(
            MasterEntry.project_id.is_(None) | (MasterEntry.project_id == project_id)
        )
        return list(self.session.scalars(query.order_by(MasterEntry.id)))

    def get_for_update(self, entry_id: uuid.UUID) -> MasterEntry | None:
        """Lock the entry while a new version is written (Postgres; SQLite ignores it)."""
        return self.session.scalars(
            self._scoped().where(MasterEntry.id == entry_id).with_for_update()
        ).one_or_none()

    def next_label_number(self, master_type: str, project_id: uuid.UUID | None) -> int:
        """The next `#n` in the entry's scope. #1 is the BAM 0.2 sample row, so admin rows start
        at #2 and are never mistaken for the (ILLUSTRATIVE) sample."""
        query = self._scoped(include_deleted=True).where(MasterEntry.master_type == master_type)
        query = query.where(
            MasterEntry.project_id.is_(None)
            if project_id is None
            else MasterEntry.project_id == project_id
        )
        numbers = [
            int(m.group(1))
            for label in self.session.scalars(query.with_only_columns(MasterEntry.row_label))
            if (m := _LABEL_NUMBER.search(label))
        ]
        return max([1, *numbers]) + 1

    # --- versions and snapshots (append-only, tenant filtered here) -------------------------

    def version(self, entry: MasterEntry, number: int | None = None) -> MasterEntryVersion | None:
        return self.session.scalars(
            select(MasterEntryVersion).where(
                MasterEntryVersion.tenant_id == self.ctx.tenant_id,
                MasterEntryVersion.entry_id == entry.id,
                MasterEntryVersion.version == (number or entry.current_version),
            )
        ).one_or_none()

    def current_versions(
        self, entries: Sequence[MasterEntry]
    ) -> dict[uuid.UUID, MasterEntryVersion]:
        if not entries:
            return {}
        wanted = {e.id: e.current_version for e in entries}
        rows = self.session.scalars(
            select(MasterEntryVersion).where(
                MasterEntryVersion.tenant_id == self.ctx.tenant_id,
                MasterEntryVersion.entry_id.in_(list(wanted)),
            )
        )
        return {r.entry_id: r for r in rows if wanted[r.entry_id] == r.version}

    def versions_at(
        self, pins: Sequence[tuple[uuid.UUID, int]]
    ) -> dict[uuid.UUID, MasterEntryVersion]:
        """The exact (entry, version) pairs a snapshot pinned, keyed by entry id."""
        if not pins:
            return {}
        wanted = dict(pins)
        rows = self.session.scalars(
            select(MasterEntryVersion).where(
                MasterEntryVersion.tenant_id == self.ctx.tenant_id,
                MasterEntryVersion.entry_id.in_(list(wanted)),
            )
        )
        return {r.entry_id: r for r in rows if wanted[r.entry_id] == r.version}

    def add_version(self, row: MasterEntryVersion) -> MasterEntryVersion:
        row.tenant_id = self.ctx.tenant_id
        self.session.add(row)
        self.session.flush()
        return row

    def snapshot(self, snapshot_id: uuid.UUID) -> MasterSnapshot | None:
        return self.session.scalars(
            select(MasterSnapshot).where(
                MasterSnapshot.tenant_id == self.ctx.tenant_id, MasterSnapshot.id == snapshot_id
            )
        ).one_or_none()

    def add_snapshot(self, row: MasterSnapshot) -> MasterSnapshot:
        row.tenant_id = self.ctx.tenant_id
        self.session.add(row)
        self.session.flush()
        return row
