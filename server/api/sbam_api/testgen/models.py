"""`test_datasets` and `test_cases` (TD §7, E13.4): a Sub-Skill's synthetic test data.

A dataset is one *version* of the data: its cases and the files its FILE refs read. Rows are only
inserted: an FDE edit writes a new version (a copy of the cases with the change) with a new
`sha256`, and the previous version stays as evidence. `version` counts from 1 per Sub-Skill. The
hash is `sbam_contracts.testgen.dataset_sha256`, the one definition the worker and the API share.
`synthetic` is always true (a CHECK, not a hope).
"""

import uuid
from typing import Any

from sbam_contracts.testgen import CaseKind, ExpectedTerminal
from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.isatvon.models  # noqa: F401 - sub_skills, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class DatasetRow(ProjectOwned, Base):
    __tablename__ = "test_datasets"
    __table_args__ = (
        UniqueConstraint("sub_skill_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("synthetic", name="synthetic_always"),
    )

    sub_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sub_skills.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    files_keys: Mapped[list[str]] = mapped_column(Json, default=list)  # content-addressed keys
    sha256: Mapped[str] = mapped_column(String(64))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    edited_by_fde: Mapped[bool] = mapped_column(Boolean, default=False)
    # what the cases were generated for (bounds, input schema, tables); None before E11.4b
    generated_for: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CaseRow(ProjectOwned, Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name"),
        status_check(CaseKind, "kind"),
        status_check(ExpectedTerminal, "expected_terminal"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.test_datasets.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    input: Mapped[dict[str, Any]] = mapped_column(Json)
    initial_state: Mapped[list[str]] = mapped_column(Json, default=list)  # seed SQL statements
    state_script: Mapped[list[list[str]]] = mapped_column(Json, default=list)
    expected_terminal: Mapped[str] = mapped_column(String(10))
    expected_output: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    assertions: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
