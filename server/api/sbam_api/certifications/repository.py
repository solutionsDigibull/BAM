"""Tenant-scoped queries for certifications (E14.1). Rows are only inserted."""

import uuid
from typing import Any

from sbam_api.certifications.models import CertificationRow
from sbam_api.db.repository import TenantScopedRepository


class CertificationRepository(TenantScopedRepository[CertificationRow]):
    """Use with a context that names the project."""

    model = CertificationRow

    def find(
        self, level: str, subject_id: uuid.UUID, content_sha256: str
    ) -> CertificationRow | None:
        query = self._scoped().where(
            CertificationRow.level == level,
            CertificationRow.subject_id == subject_id,
            CertificationRow.content_sha256 == content_sha256,
        )
        return self.session.scalars(query).first()

    def record(self, values: dict[str, Any]) -> CertificationRow:
        """Insert the certificate, or return the one an earlier attempt already wrote."""
        existing = self.find(values["level"], values["subject_id"], values["content_sha256"])
        if existing is not None:
            return existing
        row = CertificationRow(**values)
        self.add(row)
        return row
