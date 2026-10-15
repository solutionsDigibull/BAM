"""Test helpers for E10.4: SQLite with the real users, projects, project_members and
notifications tables, plus the audit/outbox and run-mirror tables the pipeline needs."""

import uuid
from collections.abc import Iterable
from typing import Any

from pipeline_db import pipeline_sessions
from sbam_api.identity.models import User
from sbam_api.notifications.models import Notification
from sbam_api.projects.models import Project, ProjectMember
from sbam_contracts.notifications import NotificationType
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

FDE_ROLE, ADMIN_ROLE = "sbam-fde", "sbam-admin"


def notification_sessions() -> sessionmaker[Session]:
    sessions = pipeline_sessions()
    engine = sessions.kw["bind"]
    tables: list[Any] = [
        User.__table__,
        Project.__table__,
        ProjectMember.__table__,
        Notification.__table__,
    ]
    Notification.metadata.create_all(engine, tables=tables)
    return sessions


def add_user(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID,
    roles: Iterable[str] = (FDE_ROLE,),
    status: str = "ACTIVE",
) -> uuid.UUID:
    with sessions() as session:
        user = User(
            tenant_id=tenant,
            keycloak_sub=f"sub-{uuid.uuid4()}",
            portal_roles=list(roles),
            status=status,
        )
        session.add(user)
        session.commit()
        return user.id


def add_project(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID,
    members: Iterable[uuid.UUID] = (),
    name: str = "Acme onboarding",
    project_id: uuid.UUID | None = None,
) -> uuid.UUID:
    with sessions() as session:
        project = Project(id=project_id or uuid.uuid4(), tenant_id=tenant, name=name, state="IDLE")
        session.add(project)
        session.flush()
        for user_id in members:
            session.add(ProjectMember(tenant_id=tenant, project_id=project.id, user_id=user_id))
        session.commit()
        return project.id


def add_notification(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID,
    user: uuid.UUID,
    *,
    title: str = "Hello",
    type: NotificationType = NotificationType.STAGE_COMPLETED,
    read: bool = False,
    project: uuid.UUID | None = None,
) -> uuid.UUID:
    from sbam_api.core.ids import uuid7
    from sbam_api.db.base import utcnow

    with sessions() as session:
        row = Notification(
            id=uuid7(),
            tenant_id=tenant,
            user_id=user,
            project_id=project,
            type=type.value,
            title=title,
            body="body",
            link=None,
            read_at=utcnow() if read else None,
            temporal_workflow_id="test",
            activity_id=str(uuid.uuid4()),
        )
        session.add(row)
        session.commit()
        return row.id


def rows(sessions: sessionmaker[Session], user: uuid.UUID | None = None) -> list[Notification]:
    with sessions() as session:
        query = select(Notification).order_by(Notification.id)
        if user is not None:
            query = query.where(Notification.user_id == user)
        return list(session.scalars(query))
