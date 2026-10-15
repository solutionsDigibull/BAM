"""Tenant-scoped project queries (E3.1) and project members (E2.4)."""

import uuid
from collections.abc import Callable, Iterable

from sbam_contracts.projects import ProjectStatus
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.db.repository import TenantContext, TenantScopedRepository
from sbam_api.projects.models import Project, ProjectMember
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


class MemberRepository(TenantScopedRepository[ProjectMember]):
    model = ProjectMember

    def user_ids(self, project_id: uuid.UUID) -> set[uuid.UUID]:
        query = self._scoped().where(ProjectMember.project_id == project_id)
        return {m.user_id for m in self.session.scalars(query)}

    def projects_of(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        query = select(ProjectMember.project_id).where(
            ProjectMember.tenant_id == self.ctx.tenant_id, ProjectMember.user_id == user_id
        )
        return frozenset(self.session.scalars(query))

    def remove(self, project_id: uuid.UUID, user_ids: Iterable[uuid.UUID]) -> None:
        self.session.execute(
            delete(ProjectMember).where(
                ProjectMember.tenant_id == self.ctx.tenant_id,
                ProjectMember.project_id == project_id,
                ProjectMember.user_id.in_(list(user_ids)),
            )
        )


class SqlMemberships:
    """`core.auth.MembershipLookup` from `project_members`. It is read on every FDE request, so an
    unassigned FDE gets 404 on their very next call."""

    def __init__(self, sessions: Callable[[], sessionmaker[Session]]) -> None:
        self.sessions = sessions

    def assigned_projects(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        with self.sessions()() as session:
            return MemberRepository(session, TenantContext(tenant_id)).projects_of(user_id)
