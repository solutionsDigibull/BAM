"""`projects`: admin-created and tenant-scoped; `state` is the ProjectPipeline state (TD §7.2).

Issue E3.1.
"""

from sbam_contracts.projects import ProjectStatus
from sbam_contracts.state_machines import MachineName
from sqlalchemy import Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, TenantOwned, status_check
from sbam_api.state_machine import Stateful, state_check


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
