"""WG-43 (#931): a build parked on the FDE's repository answer says so in `GET W/builds`.

The state is read from the audit row `repo_question_asked` (no new table). The Sub-Skill stays
BUILDING in the state machine; the row says `waiting_for` and its stage is `wait_repo_choice`.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import GateResultRow
from sbam_api.repo_choice.waiting import waiting_for
from sbam_api.temporal.models import TemporalRun
from sbam_api.testruns.models import TestRunRow
from sbam_contracts.build_dashboard import BuildsResponse
from sbam_contracts.repo_choice import REPO_REMINDER_EVERY
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_wg8_repo_choice_api import NOW
from test_wg8_repo_choice_api import Harness as RepoHarness


class Harness(RepoHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        for table in (GateResultRow.__table__, TemporalRun.__table__, TestRunRow.__table__):
            table.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]

    def builds(self, token: str | None = None, wf: Any = None) -> Any:
        return self.call("GET", self.base(wf) + "/builds", token or self.fde())

    def build_row(self) -> Any:
        res = self.builds()
        assert res.status_code == 200
        body = BuildsResponse.model_validate(res.json())  # the committed contract parses it
        return next(r for r in body.sub_skills if r.sub_skill_id == self.sub)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_wg43_t01_a_build_waiting_for_the_repository_answer_says_so(h: Harness) -> None:
    h.ask(kind="WEAK_MATCH")
    row = h.build_row()
    assert row.state == "BUILDING"
    assert row.stage == "wait_repo_choice"
    waiting = row.waiting_for
    assert waiting is not None and waiting.kind == "REPO_QUESTION"
    assert waiting.question_kind == "WEAK_MATCH"
    assert waiting.since == datetime.fromisoformat(NOW)
    assert waiting.link == f"/api/v1/projects/{h.project}/workflows/{h.wf}/repo-questions"


def test_wg43_t02_an_answered_question_is_no_longer_waiting(h: Harness) -> None:
    h.ask()
    assert h.build_row().waiting_for is not None
    h.decide(
        {
            "sub_skill_id": str(h.sub),
            "outcome": "GENERATED",
            "candidates": [],
            "decided_by": "FDE",
            "actor_id": "fde-1",
            "decided_at": NOW,
            "reason": "The FDE said no repository fits: the code is generated.",
        }
    )
    row = h.build_row()
    assert row.waiting_for is None and row.stage != "wait_repo_choice"


def test_wg43_t03_nothing_asked_or_not_building_is_not_waiting(h: Harness) -> None:
    assert h.build_row().waiting_for is None  # nothing asked
    h.ask()
    with h.sessions() as s:
        sub = s.get(SubSkill, h.sub)
        assert sub is not None
        sub.state = "CERTIFIED"
        s.commit()
    assert h.build_row().waiting_for is None


def test_wg43_t04_unassigned_cross_tenant_and_unknown_workflow_are_404(h: Harness) -> None:
    h.ask()
    problem(h.builds(h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.builds(other), 404, "NOT_FOUND")
    problem(h.builds(wf=uuid.uuid4()), 404, "NOT_FOUND")


def test_wg43_t05_the_reminder_follows_the_three_day_timer(h: Harness) -> None:
    h.ask()
    since = datetime.fromisoformat(NOW)
    assert REPO_REMINDER_EVERY == timedelta(days=3)
    for elapsed, sent in ((timedelta(hours=1), 0), (timedelta(days=3), 1), (timedelta(days=7), 2)):
        found = _waiting_at(h, since + elapsed)
        assert found.reminders_sent == sent
        assert found.next_reminder_at == since + REPO_REMINDER_EVERY * (sent + 1)


def _waiting_at(h: Harness, now: datetime) -> Any:
    ctx = TenantContext(TENANT_A, uuid.UUID(h.project))
    with h.sessions() as s:
        sub = s.get(SubSkill, h.sub)
        assert sub is not None
        found = waiting_for(s, ctx, uuid.UUID(h.project), h.wf, [sub], now=now.astimezone(UTC))
    return found[h.sub]


def test_wg43_t06_the_shown_reminder_interval_is_the_one_the_build_uses() -> None:
    from sbam_workers.pipeline.sub_skill_build import REPO_REMINDER_AFTER

    assert REPO_REMINDER_AFTER == REPO_REMINDER_EVERY


def test_wg43_t07_waiting_for_is_optional_in_the_contract(h: Harness) -> None:
    assert h.build_row().waiting_for is None  # additive: an older client never sees a new field
    schema = BuildsResponse.model_json_schema()
    assert "waiting_for" not in schema["$defs"]["BuildRow"].get("required", [])
