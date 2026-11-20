"""`project_token_caps` (#776): one row per project that has its own caps.

No row means the platform defaults (`BudgetCaps()`). `row_version` is the ETag of the PUT.
`raised_builds` holds `<SCOPE>:<build id>` for every raise already made: the raise-once flag is
build-scoped (once per scope of a build), so a Retry of the same build resumes from its saved step
under the raised cap, and a second raise for it is refused (the entry is already in the list).
"""

from typing import Any

from sbam_contracts.budget import MAX_CAP
from sqlalchemy import CheckConstraint, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.certifications.models import Json
from sbam_api.db.base import Base, ProjectOwned


def _range(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} >= 1 AND {column} <= {MAX_CAP}", name=f"{column}_in_range")


class ProjectTokenCapsRow(ProjectOwned, Base):
    __tablename__ = "project_token_caps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id"),
        _range("sub_skill_tokens"),
        _range("workflow_tokens"),
        _range("knowledge_tokens"),
        CheckConstraint(
            "first_alert_pct >= 1 AND first_alert_pct < second_alert_pct"
            " AND second_alert_pct <= 99",
            name="alerts_ordered",
        ),
        CheckConstraint("sub_skill_tokens <= workflow_tokens", name="sub_skill_within_workflow"),
    )

    sub_skill_tokens: Mapped[int] = mapped_column(Integer)
    workflow_tokens: Mapped[int] = mapped_column(Integer)
    knowledge_tokens: Mapped[int] = mapped_column(Integer)
    first_alert_pct: Mapped[int] = mapped_column(Integer)
    second_alert_pct: Mapped[int] = mapped_column(Integer)
    raised_builds: Mapped[list[Any]] = mapped_column(Json, default=list)
