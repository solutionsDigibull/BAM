"""Tenant-scoped queries for test runs (E13.6). Rows are only inserted."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.testruns.models import TestRunRow


class TestRunRepository(TenantScopedRepository[TestRunRow]):
    """Use with a context that names the project."""

    __test__ = False
    model = TestRunRow

    def of_sub_skill(self, sub_skill_id: uuid.UUID, limit: int = 100) -> list[TestRunRow]:
        """Newest first (UUIDv7 ids follow the insert order)."""
        query = (
            self._scoped()
            .where(TestRunRow.sub_skill_id == sub_skill_id)
            .order_by(TestRunRow.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def latest(self, sub_skill_id: uuid.UUID) -> TestRunRow | None:
        rows = self.of_sub_skill(sub_skill_id, limit=1)
        return rows[0] if rows else None

    def latest_of_sub_skills(self, sub_skill_ids: list[uuid.UUID]) -> dict[uuid.UUID, TestRunRow]:
        """Each Sub-Skill's newest run, in one query for the whole Build & Test grid; a Sub-Skill
        without a run is absent. Newest is the highest UUIDv7 id, as in `of_sub_skill`."""
        if not sub_skill_ids:
            return {}
        newest_first = func.row_number().over(
            partition_by=TestRunRow.sub_skill_id, order_by=TestRunRow.id.desc()
        )
        ranked = (
            self._scoped()
            .where(TestRunRow.sub_skill_id.in_(sub_skill_ids))
            .add_columns(newest_first.label("newest"))
            .subquery()
        )
        run = aliased(TestRunRow, ranked)
        rows = self.session.scalars(select(run).where(ranked.c.newest == 1))
        return {row.sub_skill_id: row for row in rows}

    def of_code_attempt(self, code_attempt_id: uuid.UUID) -> TestRunRow | None:
        """The newest run that judged this code attempt (E14.1 certifies on it)."""
        query = (
            self._scoped()
            .where(TestRunRow.code_attempt_id == code_attempt_id)
            .order_by(TestRunRow.id.desc())
            .limit(1)
        )
        return self.session.scalars(query).first()

    def for_sandbox_run(self, sandbox_run_id: uuid.UUID) -> TestRunRow | None:
        query = self._scoped().where(TestRunRow.sandbox_run_id == sandbox_run_id)
        return self.session.scalars(query).first()

    def record(self, values: dict[str, Any]) -> TestRunRow:
        """Insert the run of a sandbox run, or return the row an earlier attempt already wrote."""
        existing = self.for_sandbox_run(values["sandbox_run_id"])
        if existing is not None:
            return existing
        row = TestRunRow(**values)
        self.add(row)
        return row
