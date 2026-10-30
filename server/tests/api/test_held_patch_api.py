"""#499: the FDE decides an ISATVON patch the corrector proposed (real app and auth chain).

The held patch is read from the correction's `isatvon_corrected` audit row. A fake Temporal client
stands in for the SubSkillBuild (the real workflow is exercised in tests/temporal): it answers
`describe` and `build_status`, and records the `retry` signals the decisions send.
"""

import copy
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import record
from sbam_api.isatvon.models import SubSkill, SubSkillEdit
from sbam_contracts.build import BuildStage, SubSkillBuildStatus
from sbam_contracts.canonical import sha256_of
from sbam_contracts.runs import build_workflow_id
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy import select
from sqlalchemy.exc import OperationalError  # noqa: F401 - kept with the edit tests' imports
from temporalio.service import RPCError, RPCStatusCode
from test_auth import TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import IDEF, sample
from test_isatvon_validate_api import Harness as ValidateHarness

BUDGET = IDEF + "v_variables.budget_limit"
APPROVED_BUDGET = "5000ms timeout per validation cycle"
PATCHED_BUDGET = "9000ms timeout per validation cycle"


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
        if self.owner.current.waiting_for_retry:
            keys = [*self.owner.current.retry_keys, payload.idempotency_key]
            self.owner.current = self.owner.current.model_copy(
                update={"waiting_for_retry": False, "retry_keys": keys}
            )


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        SubSkillEdit.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        AuditLog.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.sub = self.add_sub_skill(sample(), state="NEEDS_FDE_REVIEW")
        self.fake = self.waiting(True)

    def waiting(self, waiting: bool) -> FakeBuilds:
        status = SubSkillBuildStatus(
            tenant_id=TENANT_A,
            project_id=uuid.UUID(self.project),
            workflow_id=self.wf,
            sub_skill_id=self.sub,
            build_no=1,
            state=SubSkillState.NEEDS_FDE_REVIEW,
            stage=BuildStage.WAIT_RETRY if waiting else BuildStage.AST_GATE,
            waiting_for_retry=waiting,
        )
        fake = FakeBuilds(status, self.sub)
        self.client.app.state.temporal = fake  # type: ignore[attr-defined]
        self.fake = fake
        return fake

    def patched(self, **definition_changes: Any) -> dict[str, Any]:
        out = copy.deepcopy(sample())
        for key, value in definition_changes.items():
            node = out["isatvon_definition"]
            *parents, leaf = key.split(".")
            for part in parents:
                node = node[part]
            node[leaf] = value
        return out

    def correct(self, after: dict[str, Any] | None = None, patch_class: str = "BEHAVIOUR") -> None:
        """The correct stage's audit row for a patch of the budget (a BEHAVIOUR change)."""
        after = after or self.patched(**{"v_variables.budget_limit": PATCHED_BUDGET})
        before = sample()
        with self.sessions() as s:
            record(
                s,
                tenant_id=TENANT_A,
                actor_id="sbam-corrector",
                action="isatvon_corrected",
                entity_type="sub_skill",
                entity_id=str(self.sub),
                correlation_id="corr-1",
                before={
                    "sub_skill": before,
                    "isatvon_sha256": sha256_of(before["isatvon_definition"]),
                },
                after={
                    "sub_skill": after,
                    "isatvon_sha256": sha256_of(after["isatvon_definition"]),
                    "patch": [
                        {
                            "op": "replace",
                            "path": "/isatvon_definition/v_variables/budget_limit",
                            "value": PATCHED_BUDGET,
                        }
                    ],
                    "patch_class": patch_class,
                    "changed_paths": ["/isatvon_definition/v_variables/budget_limit"],
                    "rationale": "the budget was too tight <b>for the sandbox</b>",
                    "failed_gate": "run_sandbox_tests",
                    "attempt": 1,
                },
            )
            s.commit()

    def url(self, tail: str = "", sub: uuid.UUID | None = None, wf: Any = None) -> str:
        base = f"/projects/{self.project}/workflows/{wf or self.wf}/sub-skills/{sub or self.sub}"
        return base + "/held-patch" + tail

    def etag(self) -> str:
        with self.sessions() as s:
            row = s.get(SubSkill, self.sub)
            assert row is not None
            return f'"{row.content_sha256}"'

    def decide(
        self,
        what: str,
        key: str | None = "decision-key-0001",
        if_match: str | None = "current",
        token: str | None = None,
        body: Any = None,
        **url: Any,
    ) -> Any:
        headers: dict[str, str] = {}
        if key is not None:
            headers["Idempotency-Key"] = key
        if if_match == "current":
            if_match = self.etag()
        if if_match is not None:
            headers["If-Match"] = if_match
        return self.call(
            "POST", self.url("/" + what, **url), token or self.fde(), headers=headers, json=body
        )

    def held(self, token: str | None = None, **url: Any) -> Any:
        return self.call("GET", self.url(**url), token or self.fde())

    def sub_row(self) -> SubSkill:
        with self.sessions() as s:
            row = s.get(SubSkill, self.sub)
            assert row is not None
            s.expunge(row)
            return row

    def actions(self) -> list[str]:
        with self.sessions() as s:
            return [a.action for a in s.scalars(select(AuditLog))]


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def budget(row: SubSkill) -> str:
    value: str = row.isatvon["isatvon_definition"]["v_variables"]["budget_limit"]
    return value


def test_a20b_nothing_proposed_means_nothing_held(h: Harness) -> None:
    assert h.held().json() == {"held": None}


def test_a20b_the_held_patch_is_shown_with_its_operations_and_hashes(h: Harness) -> None:
    h.correct()
    held = h.held().json()["held"]
    assert held["failed_gate"] == "run_sandbox_tests" and held["attempt"] == 1
    assert held["changed_paths"] == ["/isatvon_definition/v_variables/budget_limit"]
    assert held["ops"][0]["op"] == "replace" and held["ops"][0]["value"] == PATCHED_BUDGET
    assert (
        held["rationale"] == "the budget was too tight <b>for the sandbox</b>"
    )  # text, not markup
    assert held["isatvon_sha256_before"] != held["isatvon_sha256_after"]
    assert f'"{held["content_sha256"]}"' == h.etag()  # what accept takes as If-Match


def test_a20b_a_neutral_patch_is_never_held(h: Harness) -> None:
    h.correct(patch_class="NEUTRAL")
    assert h.held().json() == {"held": None}


def test_a20b_a_sub_skill_that_is_not_in_review_holds_nothing(h: Harness) -> None:
    h.correct()
    with h.sessions() as s:
        row = s.get(SubSkill, h.sub)
        assert row is not None
        row.state = "BUILDING"
        s.commit()
    assert h.held().json() == {"held": None}


def test_a20b_accepting_applies_the_patch_audits_it_and_retries(h: Harness) -> None:
    h.correct()
    before = h.sub_row().content_sha256
    res = h.decide("accept", body={"reason": "9 s is fine for the sandbox"})
    assert res.status_code == 202, res.text
    assert res.json() == {"sub_skill_id": str(h.sub), "build_no": 1, "decision": "ACCEPTED"}
    row = h.sub_row()
    assert budget(row) == PATCHED_BUDGET and row.content_sha256 != before
    assert row.state == "NEEDS_FDE_REVIEW" and row.validation_errors == []  # retry moves it
    with h.sessions() as s:
        edits = list(s.scalars(select(SubSkillEdit)))
        accepted = [a for a in s.scalars(select(AuditLog)) if a.action == "ISATVON_PATCH_ACCEPTED"]
    assert [(e.path, e.old, e.new) for e in edits] == [(BUDGET, APPROVED_BUDGET, PATCHED_BUDGET)]
    (audit,) = accepted
    assert audit.after is not None and audit.after["origin"] == "sbam-corrector"
    assert audit.after["reason"] == "9 s is fine for the sandbox"
    assert "ISATVON_EDIT" in h.actions()
    [(workflow, name, _)] = h.fake.signals
    assert workflow == build_workflow_id(h.sub, 1) and name == "retry"
    assert h.held().json() == {"held": None}  # a decision hides the patch


def test_a20b_rejecting_records_it_keeps_the_approved_isatvon_and_retries(h: Harness) -> None:
    h.correct()
    before = h.sub_row()
    res = h.decide("reject", body={"reason": "the budget stays"}, if_match=None)
    assert res.status_code == 202 and res.json()["decision"] == "REJECTED"
    assert (
        h.sub_row().isatvon == before.isatvon
        and h.sub_row().content_sha256 == before.content_sha256
    )
    with h.sessions() as s:
        (audit,) = [a for a in s.scalars(select(AuditLog)) if a.action == "ISATVON_PATCH_REJECTED"]
    assert audit.after is not None and audit.after["reason"] == "the budget stays"
    assert audit.after["changed_paths"] == ["/isatvon_definition/v_variables/budget_limit"]
    assert len(h.fake.signals) == 1 and h.held().json() == {"held": None}


def test_a20b_a_repeated_decision_applies_and_signals_once(h: Harness) -> None:
    h.correct()
    first = h.decide("accept", key="decision-key-0007")
    second = h.decide("accept", key="decision-key-0007", if_match=None)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert len(h.fake.signals) == 1
    assert h.actions().count("ISATVON_PATCH_ACCEPTED") == 1
    assert h.actions().count("ISATVON_EDIT") == 1


def test_a20b_accepting_needs_if_match_and_it_must_be_current(h: Harness) -> None:
    h.correct()
    problem(h.decide("accept", if_match=None), 428, "PRECONDITION_REQUIRED")
    problem(h.decide("accept", if_match='"stale"'), 412, "PRECONDITION_FAILED")
    assert budget(h.sub_row()) == APPROVED_BUDGET and h.fake.signals == []


def test_a20b_a_decision_needs_an_idempotency_key(h: Harness) -> None:
    h.correct()
    assert h.decide("accept", key=None).status_code == 400
    assert h.decide("reject", key=None, if_match=None).status_code == 400
    assert h.fake.signals == []


def test_a20b_when_the_build_is_not_waiting_nothing_is_applied(h: Harness) -> None:
    h.correct()
    h.waiting(False)
    problem(h.decide("accept"), 409, "NOT_IN_REVIEW")
    problem(h.decide("reject", key="decision-key-0002", if_match=None), 409, "NOT_IN_REVIEW")
    assert budget(h.sub_row()) == APPROVED_BUDGET
    assert "ISATVON_PATCH_ACCEPTED" not in h.actions() and h.fake.signals == []


def test_a20b_with_no_held_patch_a_decision_is_not_in_review(h: Harness) -> None:
    problem(h.decide("accept"), 409, "NOT_IN_REVIEW")
    problem(h.decide("reject", key="decision-key-0003", if_match=None), 409, "NOT_IN_REVIEW")
    assert h.fake.signals == []


def test_a20b_a_patch_made_for_another_isatvon_is_refused(h: Harness) -> None:
    h.correct()
    with h.sessions() as s:  # the approved ISATVON moved on after the correction
        row = s.get(SubSkill, h.sub)
        assert row is not None
        changed = copy.deepcopy(row.isatvon)
        changed["isatvon_definition"]["v_variables"]["budget_limit"] = "7000ms per cycle"
        row.isatvon = changed
        s.commit()
    problem(h.decide("accept"), 409, "CONFLICT")
    assert "ISATVON_PATCH_ACCEPTED" not in h.actions() and h.fake.signals == []


def test_a20b_a_patch_that_fails_validation_is_refused_and_stores_nothing(h: Harness) -> None:
    bad = h.patched(**{"v_variables.max_iterations": 9})  # the bound text no longer matches
    h.correct(after=bad)
    res = h.decide("accept")
    problem(res, 422, "VALIDATION_ERROR")
    assert res.json()["errors"]
    assert h.sub_row().isatvon == sample() and h.fake.signals == []


def test_a20b_another_tenant_workflow_or_sub_skill_is_404(h: Harness) -> None:
    h.correct()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.held(other), 404, "NOT_FOUND")
    problem(h.decide("accept", token=other), 404, "NOT_FOUND")
    problem(
        h.decide("reject", token=other, if_match=None, key="decision-key-0004"), 404, "NOT_FOUND"
    )
    problem(h.held(sub=uuid.uuid4()), 404, "NOT_FOUND")
    other_wf = h.workflow(2, sow_version=h.generated(h.structured))
    problem(h.held(wf=other_wf), 404, "NOT_FOUND")
    assert budget(h.sub_row()) == APPROVED_BUDGET and h.fake.signals == []


def test_a20b_the_hash_the_patch_reports_is_the_if_match_that_accepts_it(h: Harness) -> None:
    h.correct()
    sha = h.held().json()["held"]["content_sha256"]
    res = h.decide("accept", if_match=f'"{sha}"')
    assert res.status_code == 202, res.text
