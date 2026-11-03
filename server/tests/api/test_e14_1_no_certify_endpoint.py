"""E14.1-T08: certification is automatic only, so no endpoint certifies a Sub-Skill by hand."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.isatvon.models import SubSkill
from sbam_contracts.api import API_BASE
from sqlalchemy import func, select
from test_isatvon_validate_api import Harness, sample


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def audit_rows(h: Harness) -> int:
    with h.sessions() as s:
        return int(s.scalar(select(func.count()).select_from(AuditLog)) or 0)


@pytest.mark.parametrize("verb", ["certify", "approve", "freeze"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_e14_1_t08_no_manual_certify_route_exists(h: Harness, method: str, verb: str) -> None:
    sub = h.add_sub_skill(sample(), state="TESTING")
    before = audit_rows(h)
    path = f"/projects/{h.project}/workflows/{h.wf}/sub-skills/{sub}/{verb}"
    response = h.call(method, path, h.fde())
    assert response.status_code in (404, 405)
    assert audit_rows(h) == before  # nothing was written
    with h.sessions() as s:
        assert s.get(SubSkill, sub).state == "TESTING"  # type: ignore[union-attr]


def test_e14_1_t08_the_openapi_has_no_certify_operation(h: Harness) -> None:
    paths = h.client.get(f"{API_BASE}/openapi.json").json()["paths"]
    offenders = [
        f"{method.upper()} {path}"
        for path, item in paths.items()
        for method in item
        if method in {"post", "put", "patch"} and "certif" in path.lower()
    ]
    assert offenders == []
    assert uuid.UUID(h.project)
