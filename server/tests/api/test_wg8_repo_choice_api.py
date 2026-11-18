"""#756 (WG-8): the choose-a-repository question and the FDE's answer (real app and auth chain).

The question is read from the build's `repo_question_asked` audit row. A fake Temporal client stands
in for the SubSkillBuild (the real workflow is exercised in tests/temporal): it answers `describe`
and `build_status`, and records the `repo_answer` signals the answers send.

- WG8-T05: an FDE who is not assigned to the project (or another tenant's) gets 404, for the read
  and for the answer, and nothing is signalled.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import record
from sbam_api.isatvon.models import SubSkill
from sbam_contracts.build import BuildStage, SubSkillBuildStatus
from sbam_contracts.repo_choice import QUESTION_ASKED, REPO_ANSWER_SIGNAL
from sbam_contracts.runs import build_workflow_id
from sbam_contracts.state_machines import SubSkillState
from temporalio.service import RPCError, RPCStatusCode
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import Harness as ValidateHarness
from test_isatvon_validate_api import sample

A, B = "https://github.com/acme/alpha", "https://github.com/acme/beta"
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC).isoformat()


@dataclass
class FakeBuilds:
    current: SubSkillBuildStatus
    sub: uuid.UUID
    signals: list[tuple[str, str, Any]] = field(default_factory=list)

    def get_workflow_handle(self, workflow_id: str, **_: Any) -> "FakeHandle":
        return FakeHandle(self, workflow_id)


@dataclass
class FakeHandle:
    owner: FakeBuilds
    id: str

    async def describe(self, **_: Any) -> None:
        if self.id != build_workflow_id(self.owner.sub, 1):
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

    async def query(self, name: str, **_: Any) -> SubSkillBuildStatus:
        assert name == "build_status"
        return self.owner.current

    async def signal(self, name: str, payload: Any, **_: Any) -> None:
        self.owner.signals.append((self.id, name, payload))
        if self.owner.current.waiting_for_repo_choice:
            keys = [*self.owner.current.repo_answer_keys, payload.idempotency_key]
            self.owner.current = self.owner.current.model_copy(
                update={"waiting_for_repo_choice": False, "repo_answer_keys": keys}
            )


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        AuditLog.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.sub = self.add_sub_skill(sample(), state="BUILDING")
        self.fake = self.waiting(True)

    def waiting(self, waiting: bool) -> FakeBuilds:
        status = SubSkillBuildStatus(
            tenant_id=TENANT_A,
            project_id=uuid.UUID(self.project),
            workflow_id=self.wf,
            sub_skill_id=self.sub,
            build_no=1,
            state=SubSkillState.BUILDING,
            stage=BuildStage.WAIT_REPO if waiting else BuildStage.AST_GATE,
            waiting_for_repo_choice=waiting,
        )
        fake = FakeBuilds(status, self.sub)
        self.client.app.state.temporal = fake  # type: ignore[attr-defined]
        self.fake = fake
        return fake

    def ask(self, kind: str = "CLOSE_CANDIDATES", urls: tuple[str, ...] = (A, B)) -> None:
        """The audit row `choose_repo` writes when it starts to wait."""
        question = {
            "sub_skill_id": str(self.sub),
            "kind": kind,
            "candidates": [
                {
                    "url": u,
                    "source": "ADMIN_AGENTS",
                    "score": 0.85,
                    "reasons": [f"{u[-5:]} names the API"],
                }
                for u in urls
            ],
            "asked_at": NOW,
        }
        self.write(QUESTION_ASKED, question, f"{QUESTION_ASKED}:{self.sub}:1")

    def decide(self, after: dict[str, Any]) -> None:
        self.write("repo_decided", after, f"repo_decided:{self.sub}:1")

    def write(self, action: str, after: dict[str, Any], key: str) -> None:
        with self.sessions() as s:
            record(
                s,
                tenant_id=TENANT_A,
                actor_id="sbam-repo-chooser",
                action=action,
                entity_type="sub_skill",
                entity_id=str(self.sub),
                correlation_id="corr-1",
                after=after,
                idempotency_key=key,
            )
            s.commit()

    def base(self, wf: Any = None) -> str:
        return f"/projects/{self.project}/workflows/{wf or self.wf}"

    def questions(self, token: str | None = None, wf: Any = None) -> Any:
        return self.call("GET", self.base(wf) + "/repo-questions", token or self.fde())

    def answer(
        self,
        body: Any,
        key: str | None = "repo-answer-key-0001",
        token: str | None = None,
        sub: uuid.UUID | None = None,
        wf: Any = None,
    ) -> Any:
        headers = {} if key is None else {"Idempotency-Key": key}
        url = f"{self.base(wf)}/sub-skills/{sub or self.sub}/repo-answer"
        return self.call("POST", url, token or self.fde(), headers=headers, json=body)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_wg8_nothing_asked_means_nothing_to_decide(h: Harness) -> None:
    assert h.questions().json() == {
        "workflow_id": str(h.wf),
        "questions": [],
        "decisions": [],
    }


def test_wg8_the_question_shows_the_short_list_scores_and_reasons(h: Harness) -> None:
    h.ask()
    body = h.questions().json()
    [item] = body["questions"]
    assert item["sub_skill_name"] and item["build_no"] == 1
    question = item["question"]
    assert question["kind"] == "CLOSE_CANDIDATES" and question["sub_skill_id"] == str(h.sub)
    assert [c["url"] for c in question["candidates"]] == [A, B]
    assert question["candidates"][0]["score"] == 0.85 and question["candidates"][0]["reasons"]
    assert body["decisions"] == []


def test_wg8_a_decision_closes_the_question_and_is_listed(h: Harness) -> None:
    h.ask()
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
    body = h.questions().json()
    assert body["questions"] == []
    [decision] = body["decisions"]
    assert decision["decided_by"] == "FDE" and decision["actor_id"] == "fde-1"
    assert decision["outcome"] == "GENERATED" and decision["decided_at"].startswith("2026-11-18")


def test_wg8_a_sub_skill_that_is_no_longer_building_is_not_asking(h: Harness) -> None:
    h.ask()
    with h.sessions() as s:
        row = s.get(SubSkill, h.sub)
        assert row is not None
        row.state = "CERTIFIED"
        s.commit()
    assert h.questions().json()["questions"] == []


def test_wg8_t03_an_answer_signals_the_waiting_build_in_the_fdes_name(h: Harness) -> None:
    h.ask()
    res = h.answer({"action": "CHOOSE", "url": B})
    assert res.status_code == 202, res.text
    assert res.json() == {"sub_skill_id": str(h.sub), "build_no": 1}
    [(workflow, name, signal)] = h.fake.signals
    assert workflow == build_workflow_id(h.sub, 1) and name == REPO_ANSWER_SIGNAL
    assert (signal.action.value, signal.url) == ("CHOOSE", B)
    assert signal.idempotency_key == "repo-answer-key-0001" and signal.actor_id


def test_wg8_t04_no_repo_is_signalled_without_a_url(h: Harness) -> None:
    h.ask()
    assert h.answer({"action": "NO_REPO"}).status_code == 202
    [(_, _, signal)] = h.fake.signals
    assert signal.action.value == "NO_REPO" and signal.url is None


def test_wg8_a_repeated_key_answers_the_same_and_signals_once(h: Harness) -> None:
    h.ask()
    first = h.answer({"action": "CHOOSE", "url": A})
    again = h.answer({"action": "CHOOSE", "url": A})
    assert first.status_code == again.status_code == 202 and first.json() == again.json()
    assert len(h.fake.signals) == 1


def test_wg8_a_choice_off_the_short_list_is_422_and_signals_nothing(h: Harness) -> None:
    h.ask()
    res = h.answer({"action": "CHOOSE", "url": "https://github.com/evil/other"})
    problem(res, 422, "VALIDATION_ERROR")
    assert res.json()["errors"][0]["path"] == "url" and h.fake.signals == []


def test_wg8_with_no_candidate_the_fde_may_name_a_safe_repository_only(h: Harness) -> None:
    h.ask(kind="NO_CANDIDATE", urls=())
    for bad in ("http://github.com/me/mine", "https://localhost/me/mine", "https://10.0.0.1/a/b"):
        problem(h.answer({"action": "CHOOSE", "url": bad}), 422, "VALIDATION_ERROR")
    assert h.fake.signals == []
    assert h.answer({"action": "CHOOSE", "url": "https://github.com/me/mine"}).status_code == 202
    [(_, _, signal)] = h.fake.signals
    assert signal.url == "https://github.com/me/mine"


def test_wg8_a_malformed_answer_is_refused(h: Harness) -> None:
    h.ask()
    for body in ({"action": "CHOOSE"}, {"action": "NO_REPO", "url": A}, {"action": "MAYBE"}, {}):
        assert h.answer(body).status_code in (400, 422)
    assert h.fake.signals == []


def test_wg8_an_answer_needs_an_idempotency_key(h: Harness) -> None:
    h.ask()
    assert h.answer({"action": "NO_REPO"}, key=None).status_code == 400
    assert h.fake.signals == []


def test_wg8_a_build_that_is_not_waiting_is_409_not_in_review(h: Harness) -> None:
    h.ask()
    h.waiting(False)
    problem(h.answer({"action": "NO_REPO"}), 409, "NOT_IN_REVIEW")
    assert h.fake.signals == []


def test_wg8_an_answer_with_no_question_is_409_not_in_review(h: Harness) -> None:
    problem(h.answer({"action": "NO_REPO"}), 409, "NOT_IN_REVIEW")
    assert h.fake.signals == []


def test_wg8_t05_an_unassigned_fde_gets_404_for_the_read_and_the_answer(h: Harness) -> None:
    h.ask()
    unassigned = h.realm.token(FDE_A2)  # same tenant, not a member of the project
    problem(h.questions(unassigned), 404, "NOT_FOUND")
    problem(h.answer({"action": "NO_REPO"}, token=unassigned), 404, "NOT_FOUND")
    assert h.fake.signals == []


def test_wg8_another_tenant_workflow_or_sub_skill_is_404(h: Harness) -> None:
    h.ask()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.questions(other), 404, "NOT_FOUND")
    problem(h.answer({"action": "NO_REPO"}, token=other), 404, "NOT_FOUND")
    problem(h.answer({"action": "NO_REPO"}, sub=uuid.uuid4()), 404, "NOT_FOUND")
    other_wf = h.workflow(2, sow_version=h.generated(h.structured))
    problem(h.questions(wf=uuid.uuid4()), 404, "NOT_FOUND")
    problem(h.answer({"action": "NO_REPO"}, wf=other_wf), 404, "NOT_FOUND")  # the sub is not in it
    assert h.fake.signals == []
