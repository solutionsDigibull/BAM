"""`macro_skills` (TD §7.6, §10.2, E8.2): the Macro Skills of a Workflow, from ISATVON pass 1, and
their `sub_skills` (E8.3, pass 2; see `SubSkill`).

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


class SubSkill(Stateful, ProjectOwned, Base):
    """`sub_skills` (TD §7.6, §10.3, E8.3): one Sub-Skill of a Macro Skill, from ISATVON pass 2.

    `isatvon` is the whole ISATVON 1.4 Sub-Skill (`skill_metadata` + `isatvon_definition`), exactly
    as validated; `validation_errors` the TD §10.4 findings (`RuleViolation`s), kept with the
    content, never fixed silently. `content_sha256` is of the canonical `isatvon` JSON (the
    workbench row's ETag). `state` (SUB_SKILL machine: DRAFT → VALID | INVALID → …) changes only
    through the state-machine service.
    """

    __tablename__ = "sub_skills"
    __table_args__ = (
        state_check(MachineName.SUB_SKILL),
        UniqueConstraint("macro_skill_id", "order"),
        UniqueConstraint("workflow_id", "skill_id"),
    )
    machine = MachineName.SUB_SKILL

    macro_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.macro_skills.id"), index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    skill_id: Mapped[str] = mapped_column(String(120))  # MSnn.m_<Name>
    skill_name: Mapped[str] = mapped_column(String(200))
    order: Mapped[int] = mapped_column(Integer)
    isatvon: Mapped[dict[str, Any]] = mapped_column(Json)
    isatvon_framework_version: Mapped[str] = mapped_column(String(10), default="1.4")
    sow_refs: Mapped[list[str]] = mapped_column(Json)
    validation_errors: Mapped[list[Any]] = mapped_column(Json, default=list)
    content_sha256: Mapped[str] = mapped_column(String(64))


register_entity(MachineName.SUB_SKILL, SubSkill)


class SubSkillEdit(ProjectOwned, Base):
    """`sub_skill_edits` (TD §10, E8.4): one row per field an FDE changed, so the grid can show a
    field's history. `path` is the grid `field_path` (or `order`); `old` / `new` are the JSON
    values; `created_at` is when. Rows are only ever inserted."""

    __tablename__ = "sub_skill_edits"

    sub_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sub_skills.id"), index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    old: Mapped[Any] = mapped_column(Json)
    new: Mapped[Any] = mapped_column(Json)
    edited_by: Mapped[str] = mapped_column(String(255))
