"""`projects`: admin-created and tenant-scoped; `state` is the ProjectPipeline state (TD §7.2).
`project_members`: the FDEs assigned to a project (E2.4).

Issues E3.1, E2.4.
"""

import uuid

from sbam_contracts.projects import ProjectStatus
from sbam_contracts.state_machines import MachineName
from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, TenantOwned, status_check
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity


class Project(Stateful, TenantOwned, Base):
    """`state` (exposed as `pipeline_state`) changes only through the state-machine service."""

    __tablename__ = "projects"
    __table_args__ = (status_check(ProjectStatus), state_check(MachineName.PROJECT_PIPELINE))
    machine = MachineName.PROJECT_PIPELINE

    name: Mapped[str] = mapped_column(String(120))
    client_name: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    context_notes: Mapped[str | None] = mapped_column(Text)  # Markdown; the client sanitises it
    status: Mapped[str] = mapped_column(String(20), default=ProjectStatus.ACTIVE.value)


# Unique per tenant ignoring case; the contract already trimmed the name.
Index("uq_projects_tenant_name_ci", Project.tenant_id, func.lower(Project.name), unique=True)


class ProjectMember(TenantOwned, Base):
    """Unassigning deletes the row (no soft delete); the audit log keeps the history."""

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="FDE")


# The pipeline's transition activity finds the projects table here (E10.1).
register_entity(MachineName.PROJECT_PIPELINE, Project)
