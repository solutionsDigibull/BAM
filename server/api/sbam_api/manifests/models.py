"""`sub_skill_manifests` and `gate_results` (TD §7, E9.1).

`sub_skill_manifests`: one `SkillManifest` (TD §11.1, all six blocks) per build stage key
(sub_skill, build_no, cycle, attempt), with its deterministic hash. A frozen row (`is_frozen`) can't
change: the shared `forbid_frozen_change` trigger (migration, `db/frozen.py`) answers SB423, which
the repository maps to 423 RESOURCE_LOCKED.

`gate_results`: one gate's verdict with its findings (`build_dashboard.Finding` dumps). E9.1 writes
ISATVON_CONTRACT when a Sub-Skill fails the TD §10.4 validator at build time; the code gates (E12)
add theirs with `code_attempt_id`. A Workflow-level gate (SCHEMA_SQL, E9.4) has `workflow_id` and no
Sub-Skill; every row has one or the other.

`workflow_manifests` (E9.5): the Workflow manifest (TD §11.3) of each build, one row per
(workflow_id, build_no), with `integrity.content_sha256`. A draft until certification; frozen rows
can't change, like the Sub-Skill manifests.
"""

import uuid
from typing import Any

from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.manifest import WORKFLOW_SCHEMA_VERSION
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Connection,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.isatvon.models  # sub_skills, a foreign-key target
import sbam_api.workflows.models  # noqa: F401 - workflows, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")
# The registered JSON Schema the manifest validates against (sbam_contracts.registry).
SCHEMA_VERSION = "manifest/skill_manifest@1.0"


class SubSkillManifest(ProjectOwned, Base):
    __tablename__ = "sub_skill_manifests"
    __table_args__ = (
        # the build stage key (TD §13.4); named: the generated name passes Postgres' 63 characters
        UniqueConstraint(
            "tenant_id",
            "sub_skill_id",
            "build_no",
            "cycle",
            "attempt",
            name="uq_sub_skill_manifests_stage_key",
        ),
    )

    sub_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sub_skills.id"), index=True
    )
    build_no: Mapped[int] = mapped_column(Integer)
    cycle: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]] = mapped_column(Json)
    sha256: Mapped[str] = mapped_column(String(71))  # governance.deterministic_hash
    schema_version: Mapped[str] = mapped_column(String(40), default=SCHEMA_VERSION)
    runtime_digest: Mapped[str] = mapped_column(String(71))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class GateResultRow(ProjectOwned, Base):
    __tablename__ = "gate_results"
    __table_args__ = (
        status_check(GateName, "gate"),
        status_check(GateStatus),
        CheckConstraint("sub_skill_id IS NOT NULL OR workflow_id IS NOT NULL", name="has_subject"),
        # One result per gate per build stage key (E9.3); null keys (workflow gates, rows written
        # before the key existed) never collide.
        UniqueConstraint("sub_skill_id", "gate", "build_no", "cycle", "attempt"),
    )

    sub_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.sub_skills.id"), index=True
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.workflows.id"), index=True
    )
    code_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.code_attempts.id")
    )
    gate: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    tool_version: Mapped[str | None] = mapped_column(String(80))
    config_sha256: Mapped[str | None] = mapped_column(String(71))
    # The build stage key it belongs to (BuildStageInput), for gates that run before any
    # `code_attempts` row exists (LIBRARIES).
    build_no: Mapped[int | None] = mapped_column(Integer)
    cycle: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int | None] = mapped_column(Integer)
    # The bundle a code gate's verdict is about (E12.2); null for gates that run before one exists.
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))


def _sqlite_frozen_guard(target: Table, connection: Connection, **_: object) -> None:
    """The frozen trigger on SQLite (test databases built with `create_all`)."""
    if connection.dialect.name != "sqlite":
        return  # Postgres gets `forbid_frozen_change` from the migration
    for op in ("UPDATE", "DELETE"):
        connection.exec_driver_sql(
            f'CREATE TRIGGER "{target.name}_frozen_{op.lower()}" BEFORE {op} ON "{target.name}" '
            f"WHEN OLD.is_frozen BEGIN SELECT RAISE(ABORT, 'row is frozen'); END"
        )


event.listen(SubSkillManifest.__table__, "after_create", _sqlite_frozen_guard)


class WorkflowManifestRow(ProjectOwned, Base):
    """The Workflow manifest (TD §11.3) of one build: the E9.5 draft until certification (E14)."""

    __tablename__ = "workflow_manifests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "workflow_id", "build_no", name="uq_workflow_manifests_build"
        ),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    build_no: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]] = mapped_column(Json)
    sha256: Mapped[str] = mapped_column(String(71))  # integrity.content_sha256
    schema_version: Mapped[str] = mapped_column(String(40), default=WORKFLOW_SCHEMA_VERSION)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


event.listen(WorkflowManifestRow.__table__, "after_create", _sqlite_frozen_guard)
