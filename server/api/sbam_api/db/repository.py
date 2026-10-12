"""Tenant-scoped repository base: the only way feature code reads or writes tables (TD §7.1, §16).

Every query filters by the caller's tenant (and project, for project-owned tables when the context
has one) and skips soft-deleted rows. A row from another tenant looks exactly like a missing one:
`get` returns None and the API answers 404, so existence never leaks.
"""

import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

from sbam_contracts.api import PAGE_LIMIT_MAX, ErrorCode
from sqlalchemy import Select, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.base import ProjectOwned, TenantOwned, utcnow
from sbam_api.db.frozen import FROZEN_SQLSTATE


@dataclass(frozen=True)
class TenantContext:
    """Who is asking. Built by auth (#48) from the verified token, never from request headers."""

    tenant_id: uuid.UUID
    project_id: uuid.UUID | None = None


class TenantContextMissing(RuntimeError):  # noqa: N818 - names from the E1.5 spec
    """A repository was used without a tenant: a programming error, so fail closed."""


class StaleRowVersion(ApiError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__(
            412, ErrorCode.PRECONDITION_FAILED, "Resource changed since you loaded it; reload"
        )


class RowFrozen(ApiError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__(
            423,
            ErrorCode.RESOURCE_LOCKED,
            "Locked",
            "This resource is frozen / approved — create a new version to change it.",
        )


class TenantScopedRepository[M: TenantOwned]:
    """Subclass per table, e.g. `class ProjectRepository(TenantScopedRepository[Project])` with
    `model = Project`.

    Flush errors leave the session needing a rollback; the unit of work that owns it does that.
    """

    model: ClassVar[type[Any]]

    def __init__(self, session: Session, ctx: TenantContext | None) -> None:
        if ctx is None or not ctx.tenant_id:
            raise TenantContextMissing(f"{type(self).__name__} needs a TenantContext")
        self.session = session
        self.ctx = ctx

    @property
    def _project_owned(self) -> bool:
        return issubclass(self.model, ProjectOwned)

    def _scoped(self, include_deleted: bool = False) -> Select[tuple[M]]:
        query: Select[tuple[M]] = select(self.model).where(
            self.model.tenant_id == self.ctx.tenant_id
        )
        if self._project_owned and self.ctx.project_id is not None:
            query = query.where(self.model.project_id == self.ctx.project_id)
        if not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))
        return query

    def get(self, id: uuid.UUID, *, include_deleted: bool = False) -> M | None:
        """None for missing, soft-deleted and other-tenant rows alike."""
        return self.session.scalars(
            self._scoped(include_deleted).where(self.model.id == id)
        ).one_or_none()

    def list(self, *, include_deleted: bool = False, limit: int = PAGE_LIMIT_MAX) -> list[M]:
        """Oldest first (UUIDv7 ids sort by creation). `include_deleted`: purge job only."""
        query = self._scoped(include_deleted).order_by(self.model.id).limit(limit)
        return list(self.session.scalars(query))

    def add(self, entity: M) -> M:
        """Stamp the caller's tenant (and project) on a new row, whatever the entity carried."""
        entity.tenant_id = self.ctx.tenant_id
        if self._project_owned:
            if self.ctx.project_id is None:
                raise TenantContextMissing(f"{self.model.__name__} needs a project in the context")
            entity.project_id = self.ctx.project_id  # type: ignore[attr-defined]
        self.session.add(entity)
        self._flush()
        return entity

    def update(self, entity: M, expected_row_version: int) -> M:
        """Write the entity's pending changes if it is still at `expected_row_version` (If-Match).

        The check runs twice: here against the loaded row, and in the UPDATE's WHERE clause, which
        catches a concurrent writer that committed after this session loaded the row.
        """
        self._check_owned(entity)
        if entity.row_version != expected_row_version:
            raise StaleRowVersion()
        self._flush()
        return entity

    def soft_delete(self, entity: M) -> M:
        self._check_owned(entity)
        entity.deleted_at = utcnow()
        self._flush()
        return entity

    def _check_owned(self, entity: M) -> None:
        if entity.tenant_id != self.ctx.tenant_id:
            raise not_found()

    def _flush(self) -> None:
        try:
            self.session.flush()
        except StaleDataError:
            raise StaleRowVersion() from None
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == FROZEN_SQLSTATE:
                raise RowFrozen() from None
            raise
