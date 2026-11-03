"""#421 part 5: an admin invalidates a bad cached model answer, inside their own tenant only."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.llm.models import LlmDerivative
from sqlalchemy import select
from test_auth import FDE_A1, TENANT_A, TENANT_B, problem
from test_projects import Harness

KEY_A = "a" * 64
KEY_B = "b" * 64
KEY_OTHER_TENANT = "c" * 64
PATH = "/admin/llm-cache/invalidate"


class CacheHarness(Harness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        LlmDerivative.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.add(TENANT_A, KEY_A, "sow-generate", "1.0.0")
        self.add(TENANT_A, KEY_B, "sow-generate", "1.1.0")
        self.add(TENANT_A, "d" * 64, "isatvon", "1.0.0")
        self.add(TENANT_B, KEY_OTHER_TENANT, "sow-generate", "1.0.0")

    def add(self, tenant: uuid.UUID, key: str, prompt_id: str, version: str) -> None:
        with self.sessions() as s:
            s.add(
                LlmDerivative(
                    tenant_id=tenant,
                    cache_key=key,
                    alias="sbam-sow",
                    prompt_id=prompt_id,
                    prompt_version=version,
                    output={"x": 1},
                )
            )
            s.commit()

    def keys(self, tenant: uuid.UUID) -> set[str]:
        with self.sessions() as s:
            rows = s.scalars(select(LlmDerivative).where(LlmDerivative.tenant_id == tenant))
            return {r.cache_key for r in rows}

    def audits(self) -> list[AuditLog]:
        with self.sessions() as s:
            q = select(AuditLog).where(AuditLog.action == "LLM_DERIVATIVE_INVALIDATED")
            return list(s.scalars(q))

    def invalidate(self, body: dict[str, Any], token: str | None = None) -> Any:
        return self.call("POST", PATH, token or self.admin_a(), json=body)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[CacheHarness]:
    yield CacheHarness(tmp_path)


def test_e421_t01_one_answer_is_invalidated_by_its_key(h: CacheHarness) -> None:
    res = h.invalidate({"cache_key": KEY_A})
    assert res.status_code == 200 and res.json() == {"invalidated": 1}
    assert KEY_A not in h.keys(TENANT_A) and KEY_B in h.keys(TENANT_A)


def test_e421_t02_every_answer_of_a_prompt_version_goes_and_nothing_else(h: CacheHarness) -> None:
    res = h.invalidate({"prompt_id": "sow-generate", "prompt_version": "1.0.0"})
    assert res.json() == {"invalidated": 1}
    assert h.keys(TENANT_A) == {KEY_B, "d" * 64}


def test_e421_t03_a_prompt_without_a_version_removes_all_its_versions(h: CacheHarness) -> None:
    assert h.invalidate({"prompt_id": "sow-generate"}).json() == {"invalidated": 2}
    assert h.keys(TENANT_A) == {"d" * 64}


def test_e421_t04_another_tenants_key_is_a_404_and_its_answer_stays(h: CacheHarness) -> None:
    res = h.invalidate({"cache_key": KEY_OTHER_TENANT})
    assert res.status_code == 404
    assert h.keys(TENANT_B) == {KEY_OTHER_TENANT}
    assert h.audits() == []  # nothing happened, nothing audited


def test_e421_t05_a_prompt_invalidation_never_touches_another_tenant(h: CacheHarness) -> None:
    h.invalidate({"prompt_id": "sow-generate"})
    assert h.keys(TENANT_B) == {KEY_OTHER_TENANT}


def test_e421_t06_an_unknown_key_is_a_404_and_an_unknown_prompt_is_a_200_with_zero(
    h: CacheHarness,
) -> None:
    assert h.invalidate({"cache_key": "e" * 64}).status_code == 404
    assert h.invalidate({"prompt_id": "nothing-cached"}).json() == {"invalidated": 0}


def test_e421_t07_an_fde_cannot_invalidate(h: CacheHarness) -> None:
    token = h.realm.token(FDE_A1, realm_access={"roles": ["sbam-fde"]})
    res = h.invalidate({"cache_key": KEY_A}, token)
    problem(res, 403, "WRONG_PORTAL")
    assert KEY_A in h.keys(TENANT_A)


def test_e421_t08_the_call_is_audited_with_the_selector_and_count_not_the_content(
    h: CacheHarness,
) -> None:
    h.invalidate({"prompt_id": "sow-generate"})
    (row,) = h.audits()
    assert row.tenant_id == TENANT_A and row.after is not None
    assert row.after["invalidated"] == 2 and row.after["prompt_id"] == "sow-generate"
    assert "output" not in str(row.after)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"cache_key": KEY_A, "prompt_id": "sow-generate"},
        {"cache_key": "not-a-hash"},
        {"prompt_version": "1.0.0"},
        {"cache_key": KEY_A, "prompt_version": "1.0.0"},
        {"prompt_id": "x", "extra": 1},
    ],
)
def test_e421_t09_a_body_that_names_no_single_target_is_a_422(
    h: CacheHarness, body: dict[str, Any]
) -> None:
    assert h.invalidate(body).status_code == 422
    assert len(h.keys(TENANT_A)) == 3


def test_e421_t10_no_token_is_a_401(h: CacheHarness) -> None:
    res = h.client.post(f"/api/v1{PATH}", json={"cache_key": KEY_A})
    assert res.status_code == 401
