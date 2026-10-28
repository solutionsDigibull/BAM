"""`workflows` (TD §7, E8.1): the Workflow SBAM generated from an approved SOW version.

Only SBAM creates one, from the `create_workflow` activity once the SOW approval reached the
pipeline (`created_by` is always SYSTEM; the API has no create route). One per approved SOW version
(unique project + version), numbered MAJOR.0.PATCH: each newly approved SOW version is the next
MAJOR (`major` keeps it sortable), patches of a frozen Workflow raise PATCH. `state` changes only
through the state-machine service; the name is the one field a person may change.
"""

import uuid

from sbam_contracts.state_machines import MachineName
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.sow.models  # noqa: F401 - sow_versions, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity

SYSTEM = "SYSTEM"


class Workflow(Stateful, ProjectOwned, Base):
    """`state` changes only through the state-machine service."""

    __tablename__ = "workflows"
    __table_args__ = (
        state_check(MachineName.WORKFLOW),
        UniqueConstraint("project_id", "sow_version_id"),
        UniqueConstraint("project_id", "major"),
    )
    machine = MachineName.WORKFLOW

    sow_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.sow_versions.id"))
    major: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(20))  # "MAJOR.0.PATCH"
    name: Mapped[str] = mapped_column(String(160))
    # Why the last extraction failed (E8.2): a code and ids, never document text.
    error_reason: Mapped[str | None] = mapped_column(String(300))


register_entity(MachineName.WORKFLOW, Workflow)
