"""Tenant-scoped project queries (E3.1)."""

import uuid
from collections.abc import Iterable

from sbam_contracts.projects import ProjectStatus
from sqlalchemy import func

from sbam_api.projects.models import Project
from sbam_api.state_machine.repository import StatefulRepository


class ProjectRepository(StatefulRepository):
    model = Project

    def name_taken(self, name: str, exclude: uuid.UUID | None = None) -> bool:
        """Case-insensitive, in the caller's tenant (the contract already trimmed `name`)."""
        query = self._scoped(include_deleted=True).where(func.lower(Project.name) == name.lower())
        if exclude is not None:
            query = query.where(Project.id != exclude)
        return self.session.scalars(query.limit(1)).first() is not None

    def active_among(self, ids: Iterable[uuid.UUID]) -> list[Project]:
        """The ACTIVE projects among `ids` (an FDE's assignments), oldest first."""
        query = self._scoped().where(
            Project.id.in_(list(ids)), Project.status == ProjectStatus.ACTIVE.value
        )
        return list(self.session.scalars(query.order_by(Project.id)))
