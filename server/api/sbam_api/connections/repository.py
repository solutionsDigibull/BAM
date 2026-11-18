"""Tenant-scoped queries for project connections (#764). Use with a context that names the project:
another tenant's or another project's row is a missing row (the API answers 404)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbam_api.connections.models import ConnectionRow
from sbam_api.db.repository import TenantScopedRepository


class ConnectionRepository(TenantScopedRepository[ConnectionRow]):
    model = ConnectionRow

    def by_name(self, name: str, *, for_update: bool = False) -> ConnectionRow | None:
        query = self._scoped().where(ConnectionRow.name == name)
        if for_update:
            query = query.with_for_update()
        return self.session.scalars(query).one_or_none()

    def all_rows(self) -> list[ConnectionRow]:
        """Every entry of the project, by name."""
        return list(self.session.scalars(self._scoped().order_by(ConnectionRow.name)))

    def with_value(self) -> list[ConnectionRow]:
        return [row for row in self.all_rows() if row.has_value]

    def not_under(
        self, key_id: str, *, limit: int, after: uuid.UUID | None = None
    ) -> list[ConnectionRow]:
        """Rows whose value is sealed under another key, by id, for the re-encrypt. Of the context's
        scope (a tenant, or one project when the context names it). `after`: the last id seen, so a
        row that cannot be moved is not returned again."""
        query = self._scoped().where(
            ConnectionRow.ciphertext.is_not(None), ConnectionRow.key_id != key_id
        )
        if after is not None:
            query = query.where(ConnectionRow.id > after)
        return list(self.session.scalars(query.order_by(ConnectionRow.id).limit(limit)))


def scopes_not_under(session: Session, key_id: str) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Operator use only (rekey): the (tenant, project) pairs that still hold a value under another
    key. Reads ids, never a value; the rows themselves are then read through a repository scoped to
    each pair."""
    query = (
        select(ConnectionRow.tenant_id, ConnectionRow.project_id)
        .where(
            ConnectionRow.ciphertext.is_not(None),
            ConnectionRow.key_id != key_id,
            ConnectionRow.deleted_at.is_(None),
        )
        .distinct()
        .order_by(ConnectionRow.tenant_id, ConnectionRow.project_id)
    )
    return [(tenant, project) for tenant, project in session.execute(query)]
