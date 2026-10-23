"""`macro_skills` (TD §7.6, §10.2, E8.2): the Macro Skills of a Workflow, from ISATVON pass 1.

ISATVON's "Agent": `macro_category` "MSnn_<Name>" (nn by `order`, assigned by SBAM, unique in the
Workflow), the SOW sections it covers (`sow_refs`: module / feature ids of the approved version) and
an optional Agents-master entry (`agent_ref`, AG-*). `validation_errors` keeps the warnings SBAM
recorded while storing it. `state` (AGENT machine: PENDING → BUILDING → CERTIFIED → FROZEN)
changes only through the state-machine service.
"""

import uuid
from typing import Any

from sbam_contracts.state_machines import MachineName
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.workflows.models  # noqa: F401 - workflows, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity

Json = JSON().with_variant(JSONB(), "postgresql")


class MacroSkill(Stateful, ProjectOwned, Base):
    """`state` changes only through the state-machine service."""

    __tablename__ = "macro_skills"
    __table_args__ = (
        state_check(MachineName.AGENT),
        UniqueConstraint("workflow_id", "macro_category"),
        UniqueConstraint("workflow_id", "order"),
    )
    machine = MachineName.AGENT

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    sow_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.sow_versions.id"))
    macro_category: Mapped[str] = mapped_column(String(120))  # MSnn_<Name>, the name part capped
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer)
    sow_refs: Mapped[list[str]] = mapped_column(Json)
    agent_ref: Mapped[str | None] = mapped_column(String(80))
    validation_errors: Mapped[list[Any]] = mapped_column(Json, default=list)


register_entity(MachineName.AGENT, MacroSkill)
