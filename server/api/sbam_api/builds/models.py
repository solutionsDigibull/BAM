"""`code_attempts`: one row per generation or correction of a Sub-Skill's code (TD §7.7, E11.2).

`(sub_skill_id, build_no, cycle, attempt)` is the build stage's idempotency key (TD §13.4), so a
retried activity finds its row instead of adding a second one. E11.2 writes the LLM bodies, the
`llm_calls` ids that produced them and the template used; assembly (E11.3) adds `bundle_key`,
`files`, `bundle_sha256` and the Black version + config hash that formatted it, the correction
loop (E11.4) adds `correction_reason`.

`gate_results` (E12.2, TD §7.7) holds one row per gate check of one attempt's bundle, keyed to the
`bundle_sha256` it judged, so a result for another bundle is never reused.

`sub_skill_id` and `manifest_id` carry no foreign key yet: `sub_skills` and `sub_skill_manifests`
arrive with #91.
"""

import uuid
from enum import StrEnum
from typing import Any

from sbam_contracts.build_dashboard import GateName, GateStatus
from sqlalchemy import JSON, CheckConstraint, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class CodeAttemptStatus(StrEnum):
    BODIES_GENERATED = "BODIES_GENERATED"  # bodies passed the pre-check, ready to assemble
    PRECHECK_FAILED = "PRECHECK_FAILED"  # bodies rejected before assembly -> correction
    ASSEMBLED = "ASSEMBLED"  # E11.3: Black-formatted bundle stored, `bundle_key` + `files` set
    ASSEMBLY_FAILED = "ASSEMBLY_FAILED"  # E11.3: a file did not parse (AST_SYNTAX) -> correction


class CodeAttempt(ProjectOwned, Base):
    __tablename__ = "code_attempts"
    __table_args__ = (
        status_check(CodeAttemptStatus),
        CheckConstraint("attempt >= 0 AND attempt <= 2", name="attempt_range"),
        UniqueConstraint("tenant_id", "sub_skill_id", "build_no", "cycle", "attempt"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    sub_skill_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    manifest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    build_no: Mapped[int] = mapped_column(Integer)
    cycle: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)  # 0 first pass, 1..2 after a correction
    template_name: Mapped[str] = mapped_column(String(100))
    template_version: Mapped[str] = mapped_column(String(40))
    template_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    # CodeBodies as validated: {action_body, check_body, state_queries, helpers}
    bodies: Mapped[dict[str, Any]] = mapped_column(Json)
    llm_call_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    trace_id: Mapped[str] = mapped_column(String(64))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    bundle_key: Mapped[str | None] = mapped_column(String(1024))  # E11.3
    files: Mapped[dict[str, str] | None] = mapped_column(Json)  # E11.3: path -> sha256
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))  # E11.3: RFC 8785 of `files`
    black_version: Mapped[str | None] = mapped_column(String(40))  # E11.3
    black_config_sha256: Mapped[str | None] = mapped_column(String(64))  # E11.3
    correction_reason: Mapped[str | None] = mapped_column(Text)  # E11.4


class GateResult(ProjectOwned, Base):
    """One gate check of one code attempt (AST_SYNTAX, AST_SKELETON, ...); #119 reuses the table."""

    __tablename__ = "gate_results"
    __table_args__ = (
        status_check(GateStatus),
        status_check(GateName, column="gate"),
        UniqueConstraint("tenant_id", "code_attempt_id", "gate", "bundle_sha256"),
    )

    code_attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    gate: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    tool_version: Mapped[str] = mapped_column(String(60))  # the gate's own version
    config_sha256: Mapped[str] = mapped_column(String(64))  # RFC 8785 of the gate's rule set
    bundle_sha256: Mapped[str] = mapped_column(String(64))  # the bundle the verdict is about
