"""`test_runs` (TD §7, E13.6): what Promptfoo concluded about one sandbox run.

Rows are only inserted: a run is evidence. One row per `sandbox_run_id` (a retried activity finds
its row instead of adding a second). `sandbox_run_id` is a plain id: `sandbox_runs` is the sandbox
adapter's table (E13.2), so this one does not depend on it; `sub_skill_id` and `code_attempt_id` say
which build the run judged. `results` holds one entry per case per assertion.
"""

import uuid
from typing import Any

from sbam_contracts.promptfoo import TestRunState
from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.builds.models
import sbam_api.isatvon.models  # noqa: F401 - sub_skills, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class TestRunRow(ProjectOwned, Base):
    __test__ = False
    __tablename__ = "test_runs"
    __table_args__ = (
        status_check(TestRunState, "state"),
        CheckConstraint("total >= 0 AND passed >= 0 AND failed >= 0", name="counts_not_negative"),
        CheckConstraint("passed + failed = total", name="counts_add_up"),
        CheckConstraint(
            "state <> 'PASS' OR (failed = 0 AND NOT non_certifying)", name="pass_is_green"
        ),
    )

    sub_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sub_skills.id"), index=True
    )
    code_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.code_attempts.id"), index=True
    )
    sandbox_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    suite_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    total: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    failed: Mapped[int] = mapped_column(Integer)
    non_certifying: Mapped[bool] = mapped_column(Boolean, default=False)
    results: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    report_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
