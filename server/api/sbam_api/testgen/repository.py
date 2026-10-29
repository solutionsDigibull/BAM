"""Tenant-scoped queries for test datasets and their cases (E13.4). Rows are only inserted."""

import uuid

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.testgen.models import CaseRow, DatasetRow


class DatasetRepository(TenantScopedRepository[DatasetRow]):
    """Use with a context that names the project."""

    model = DatasetRow

    def latest(self, sub_skill_id: uuid.UUID) -> DatasetRow | None:
        """The newest version of the Sub-Skill's test data, or None before any is generated."""
        query = (
            self._scoped()
            .where(DatasetRow.sub_skill_id == sub_skill_id)
            .order_by(DatasetRow.version.desc())
            .limit(1)
        )
        return self.session.scalars(query).first()

    def of_sub_skill(self, sub_skill_id: uuid.UUID) -> list[DatasetRow]:
        """Every version, oldest first (the evidence of what was tested when)."""
        query = self._scoped().where(DatasetRow.sub_skill_id == sub_skill_id)
        return list(self.session.scalars(query.order_by(DatasetRow.version)))


class CaseRepository(TenantScopedRepository[CaseRow]):
    model = CaseRow

    def of_dataset(self, dataset_id: uuid.UUID) -> list[CaseRow]:
        """In the order they were written (UUIDv7 ids follow it)."""
        query = self._scoped().where(CaseRow.dataset_id == dataset_id)
        return list(self.session.scalars(query.order_by(CaseRow.id)))
