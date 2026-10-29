"""E13.6 test runs endpoint through the real app and auth chain: the Sub-Skill's Promptfoo runs, the
grid badge helper, idempotent inserts and the CHECKs that keep a run honest."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.builds.models import CodeAttempt
from sbam_api.db.repository import TenantContext
from sbam_api.testruns.models import TestRunRow
from sbam_api.testruns.repository import TestRunRepository
from sbam_api.testruns.service import loop_tests_badge
from sbam_contracts.promptfoo import LoopTestsBadge, TestRunState
from sqlalchemy.exc import IntegrityError
from test_auth import TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import Harness as ValidateHarness
from test_isatvon_validate_api import sample


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (CodeAttempt, TestRunRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.sub = self.add_sub_skill(sample(), state="VALID")
        self.ctx = TenantContext(TENANT_A, uuid.UUID(self.project))

    def values(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "sub_skill_id": self.sub,
            "code_attempt_id": uuid.uuid4(),
            "sandbox_run_id": uuid.uuid4(),
            "suite_sha256": "a" * 64,
            "state": "PASS",
            "total": 3,
            "passed": 3,
            "failed": 0,
            "non_certifying": False,
            "results": [],
            "created_by": "test",
        }
        return {**base, **over}

    def record(self, **over: Any) -> uuid.UUID:
        with self.sessions() as s:
            row = TestRunRepository(s, self.ctx).record(self.values(**over))
            s.commit()
            return row.id

    def runs(self, token: str | None = None, sub: uuid.UUID | None = None, wf: Any = None) -> Any:
        path = f"/projects/{self.project}/workflows/{wf or self.wf}/sub-skills/{sub or self.sub}"
        return self.call("GET", path + "/test-runs", token or self.fde())


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e13_6_a_sub_skill_with_no_runs_gets_an_empty_list_and_no_badge(h: Harness) -> None:
    assert h.runs().json() == {"runs": []}
    with h.sessions() as s:
        assert loop_tests_badge(s, h.ctx, h.sub) is None


def test_e13_6_t02_the_runs_come_back_newest_first_and_the_badge_follows_the_newest(
    h: Harness,
) -> None:
    h.record(state="FAIL", passed=2, failed=1)
    newest = h.record()
    runs = h.runs().json()["runs"]
    assert runs[0]["id"] == str(newest) and len(runs) == 2
    assert runs[0]["state"] == "PASS" and runs[1]["state"] == "FAIL"
    with h.sessions() as s:
        assert loop_tests_badge(s, h.ctx, h.sub) == LoopTestsBadge(
            state=TestRunState.PASS, passed=3, total=3
        )


def test_e13_6_the_results_carry_each_case_and_assertion(h: Harness) -> None:
    results = [{"case": "happy", "type": "terminal_state", "passed": True, "message": ""}]
    h.record(results=results)
    assert h.runs().json()["runs"][0]["results"] == results


def test_e13_6_a_retried_activity_finds_its_row_instead_of_adding_a_second(h: Harness) -> None:
    sandbox_run = uuid.uuid4()
    first = h.record(sandbox_run_id=sandbox_run)
    assert h.record(sandbox_run_id=sandbox_run, state="FAIL", passed=0, failed=3) == first
    assert len(h.runs().json()["runs"]) == 1


def test_e13_6_a_local_docker_run_is_non_certifying_never_a_pass(h: Harness) -> None:
    h.record(state="NON_CERTIFYING", non_certifying=True)
    with h.sessions() as s:
        badge = loop_tests_badge(s, h.ctx, h.sub)
    assert badge is not None and badge.state is TestRunState.NON_CERTIFYING
    with pytest.raises(IntegrityError):
        h.record(state="PASS", non_certifying=True)


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "PASS", "passed": 2, "failed": 1},  # a failed case is not a pass
        {"passed": 1, "failed": 1},  # does not add up to total
        {"state": "MAYBE"},  # outside the enum
    ],
)
def test_e13_6_the_database_refuses_a_run_that_contradicts_itself(
    h: Harness, bad: dict[str, Any]
) -> None:
    with pytest.raises(IntegrityError):
        h.record(**bad)


def test_e13_6_another_tenant_or_workflow_or_unknown_sub_skill_is_404(h: Harness) -> None:
    h.record()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.runs(other), 404, "NOT_FOUND")
    problem(h.runs(sub=uuid.uuid4()), 404, "NOT_FOUND")
    other_wf = h.workflow(2, sow_version=h.generated(h.structured))
    problem(h.runs(wf=other_wf), 404, "NOT_FOUND")
