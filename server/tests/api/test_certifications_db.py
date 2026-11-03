"""E14.1: the `certifications` table: idempotent on (level, subject, hash), never changed."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sbam_api.certifications.models import CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.db.repository import TenantContext
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_auth import TENANT_A, TENANT_B
from test_isatvon_validate_api import Harness as ValidateHarness


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        CertificationRow.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.ctx = TenantContext(TENANT_A, uuid.UUID(self.project))
        self.subject = uuid.uuid4()

    def values(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "level": "SUB_SKILL",
            "subject_id": self.subject,
            "content_sha256": "sha256:" + "a" * 64,
            "evidence": {"code_attempt_id": str(uuid.uuid4())},
            "certified_at": datetime.now(UTC),
            "created_by": "test",
        }
        return {**base, **over}

    def record(self, ctx: TenantContext | None = None, **over: Any) -> uuid.UUID:
        with self.sessions() as s:
            row = CertificationRepository(s, ctx or self.ctx).record(self.values(**over))
            s.commit()
            return row.id


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e14_1_t07_a_retried_certify_finds_its_row_instead_of_adding_a_second(h: Harness) -> None:
    first = h.record()
    assert h.record(evidence={"other": "run"}) == first


def test_e14_1_a_changed_content_hash_is_a_new_certificate(h: Harness) -> None:
    assert h.record() != h.record(content_sha256="sha256:" + "b" * 64)


def test_e14_1_a_certificate_is_never_changed_or_removed(h: Harness) -> None:
    row_id = h.record()
    with h.sessions() as s, pytest.raises(DBAPIError):
        s.get(CertificationRow, row_id).evidence = {"edited": True}  # type: ignore[union-attr]
        s.commit()
    with h.sessions() as s, pytest.raises(DBAPIError):
        s.delete(s.get(CertificationRow, row_id))
        s.commit()


def test_e14_1_a_certificate_is_only_visible_inside_its_tenant(h: Harness) -> None:
    h.record()
    other = TenantContext(TENANT_B, uuid.UUID(h.project))
    with h.sessions() as s:
        assert (
            CertificationRepository(s, other).find("SUB_SKILL", h.subject, "sha256:" + "a" * 64)
            is None
        )


def test_e14_1_an_unknown_level_is_rejected(h: Harness) -> None:
    with pytest.raises(IntegrityError):
        h.record(level="PLANET")
