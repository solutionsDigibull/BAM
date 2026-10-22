"""`code_attempts`: one row per generation or correction of a Sub-Skill's code (TD §7.7, E11.2).

`(sub_skill_id, build_no, cycle, attempt)` is the build stage's idempotency key (TD §13.4), so a
retried activity finds its row instead of adding a second one. E11.2 writes the LLM bodies, the
`llm_calls` ids that produced them and the template used; assembly (E11.3) adds `bundle_key` and
`files`, the correction loop (E11.4) adds `correction_reason`.

`sub_skill_id` and `manifest_id` carry no foreign key yet: `sub_skills` and `sub_skill_manifests`
arrive with #91.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class CodeAttemptStatus(StrEnum):
    BODIES_GENERATED = "BODIES_GENERATED"  # bodies passed the pre-check, ready to assemble
    PRECHECK_FAILED = "PRECHECK_FAILED"  # bodies rejected before assembly -> correction


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
    correction_reason: Mapped[str | None] = mapped_column(Text)  # E11.4
