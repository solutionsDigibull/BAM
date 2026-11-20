"""WG-23 (#776): Admin caps per project, raise a cap once per build, and the workbench meter.

Through the real app and auth chain on SQLite. T02 also runs the worker's budget over the same
database, so "a raise lets Retry run" is the real check that refused the call, not a stub.
"""

import asyncio
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from llm_db import scope, settings
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.llm.models import LlmCall
from sbam_api.token_budget.models import ProjectTokenCapsRow
from sbam_contracts.budget import BudgetCaps
from sbam_workers.llm.budget import STOP_REASON, Budget, BudgetExceeded
from sbam_workers.llm.store import SqlCallStore
from sqlalchemy import select
from test_auth import FDE_A1, FDE_A2, TENANT_A, TENANT_B, problem
from test_projects import Harness as ProjectsHarness

CAPS = {
    "sub_skill_tokens": 500,
    "workflow_tokens": 1000,
    "knowledge_tokens": 2000,
    "first_alert_pct": 50,
    "second_alert_pct": 80,
}


class CapsHarness(ProjectsHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (LlmCall, ProjectTokenCapsRow):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.p1 = self.create("Quote Bot")["id"]
        self.p2 = self.create("Other Bot")["id"]
        self.members.assigned[FDE_A1] = {uuid.UUID(self.p1)}
        res = self.call("POST", "/admin/projects", self.admin_b(), json={"name": "Foreign Bot"})
        self.pb = res.json()["id"]

    def fde(self) -> str:
        return self.realm.token(FDE_A1)

    def caps(self, project: str, token: str | None = None) -> Any:
        return self.call("GET", f"/admin/projects/{project}/token-caps", token or self.admin_a())

    def put(
        self, project: str, body: dict[str, Any], if_match: str | None, token: str | None = None
    ) -> Any:
        headers = {} if if_match is None else {"If-Match": if_match}
        return self.call(
            "PUT",
            f"/admin/projects/{project}/token-caps",
            token or self.admin_a(),
            headers=headers,
            json=body,
        )

    def calls(self, project: str, workflow: str, tokens: int, alias: str = "sbam-codegen") -> None:
        with self.sessions() as s:
            s.add(
                LlmCall(
                    id=uuid7(),
                    tenant_id=TENANT_A if project != self.pb else TENANT_B,
                    project_id=uuid.UUID(project),
                    alias=alias,
                    model="m",
                    prompt_id="p",
                    prompt_version="1",
                    prompt_sha256="0" * 64,
                    tokens_in=tokens,
                    tokens_out=0,
                    cost_usd=Decimal(0),
                    latency_ms=1,
                    attempt=1,
                    status="SUCCEEDED",
                    trace_id="t",
                    context_manifest={},
                    correlation_id="c",
                    temporal_workflow_id=workflow,
                    activity_id=f"a-{uuid.uuid4()}",
                    activity_attempt=1,
                    output={"x": 1},
                )
            )
            s.commit()

    def raise_(
        self, project: str, scope_: str, build: str, tokens: int, token: str | None = None
    ) -> Any:
        return self.call(
            "POST",
            f"/projects/{project}/token-budget/raise",
            token or self.fde(),
            json={"scope": scope_, "build_id": build, "tokens": tokens},
        )

    def meter(self, project: str, token: str | None = None) -> Any:
        return self.call("GET", f"/projects/{project}/token-budget", token or self.fde())

    def trail(self, project: str, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            query = select(AuditLog).where(
                AuditLog.entity_type == "project_token_caps",
                AuditLog.entity_id == project,
                AuditLog.action == action,
            )
            return list(s.scalars(query.order_by(AuditLog.id)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[CapsHarness]:
    yield CapsHarness(tmp_path)


def stopped(h: CapsHarness, build: str = "build-s1-1") -> str:
    """Caps 500/1000/2000, then a build that used 1200 tokens: the cap is reached."""
    assert h.put(h.p1, CAPS, '"0"').status_code == 200
    h.calls(h.p1, build, 1200)
    return build


def test_wg23_t01_put_caps_changes_the_limit_for_that_project_only(h: CapsHarness) -> None:
    first = h.caps(h.p1)
    assert first.status_code == 200 and first.json()["is_default"] is True
    assert first.json()["row_version"] == 0 and first.headers["etag"] == '"0"'
    res = h.put(h.p1, CAPS, '"0"')
    assert res.status_code == 200, res.text
    assert res.json()["caps"] == CAPS and res.json()["is_default"] is False
    assert res.headers["etag"] == '"1"'
    assert h.caps(h.p1).json()["caps"] == CAPS
    other = h.caps(h.p2).json()
    assert other["is_default"] is True and other["caps"] == BudgetCaps().model_dump()
    [row] = h.trail(h.p1, "TOKEN_CAPS_CHANGED")
    assert row.before == BudgetCaps().model_dump() and row.after == CAPS
    assert h.trail(h.p2, "TOKEN_CAPS_CHANGED") == []


def test_wg23_t01_a_second_put_needs_the_current_etag(h: CapsHarness) -> None:
    assert h.put(h.p1, CAPS, '"0"').status_code == 200
    problem(h.put(h.p1, CAPS, None), 428, "PRECONDITION_REQUIRED")
    problem(h.put(h.p1, CAPS, '"0"'), 412, "PRECONDITION_FAILED")
    changed = h.put(h.p1, {**CAPS, "workflow_tokens": 3000}, '"1"')
    assert changed.status_code == 200 and changed.headers["etag"] == '"2"'


@pytest.mark.parametrize(
    "bad",
    [
        {**CAPS, "first_alert_pct": 90},
        {**CAPS, "sub_skill_tokens": 5000},
        {**CAPS, "workflow_tokens": 0},
        {**CAPS, "workflow_tokens": 10**10},
        {**CAPS, "extra": 1},
    ],
    ids=["alerts-unordered", "sub-above-workflow", "zero", "over-max", "unknown-field"],
)
def test_wg23_t01_bad_caps_are_refused_and_nothing_is_stored(
    h: CapsHarness, bad: dict[str, Any]
) -> None:
    assert h.put(h.p1, bad, '"0"').status_code == 422
    assert h.caps(h.p1).json()["is_default"] is True


def test_wg23_t02_a_raise_lets_retry_run(h: CapsHarness) -> None:
    """The worker's own budget refuses the stopped build, then lets it through after the raise."""
    build = stopped(h)
    store = SqlCallStore(h.sessions)
    budget = Budget(settings(), store)  # no fixed caps: it reads the project's row
    sc = scope(tenant_id=TENANT_A, project_id=uuid.UUID(h.p1), temporal_workflow_id=build)
    with pytest.raises(BudgetExceeded, match=STOP_REASON):
        asyncio.run(budget.check_tokens(sc, "sbam-codegen"))
    res = h.raise_(h.p1, "WORKFLOW", build, 5000)
    assert res.status_code == 200, res.text
    assert res.json()["cap"] == 5000 and res.json()["raised_once"] is True
    with pytest.raises(
        BudgetExceeded, match="sub-skill build"
    ):  # the build's own cap still stops it
        asyncio.run(budget.check_tokens(sc, "sbam-codegen"))
    assert h.raise_(h.p1, "SUB_SKILL", build, 2000).status_code == 200  # once per scope
    asyncio.run(budget.check_tokens(sc, "sbam-codegen"))  # Retry: no longer refused
    row = h.trail(h.p1, "TOKEN_CAP_RAISED")[0]
    assert row.actor_id == str(FDE_A1)
    assert row.before == {"scope": "WORKFLOW", "cap": 1000, "build_id": build}
    assert row.after == {"scope": "WORKFLOW", "cap": 5000, "build_id": build}


def test_wg23_t02_an_admin_can_raise_too(h: CapsHarness) -> None:
    build = stopped(h)
    res = h.call(
        "POST",
        f"/admin/projects/{h.p1}/token-budget/raise",
        h.admin_a(),
        json={"scope": "WORKFLOW", "build_id": build, "tokens": 4000},
    )
    assert res.status_code == 200, res.text
    assert h.caps(h.p1).json()["caps"]["workflow_tokens"] == 4000


def test_wg23_t03_a_second_raise_is_refused_and_audited(h: CapsHarness) -> None:
    build = stopped(h)
    assert h.raise_(h.p1, "WORKFLOW", build, 1500).status_code == 200
    h.calls(h.p1, build, 1000)  # 2200 now: stopped again under the raised 1500
    again = h.raise_(h.p1, "WORKFLOW", build, 9000)
    problem(again, 409, "CONFLICT")
    assert "once already" in again.json()["detail"]
    assert h.caps(h.p1).json()["caps"]["workflow_tokens"] == 1500  # unchanged
    [refused] = h.trail(h.p1, "TOKEN_CAP_RAISE_REFUSED")
    assert refused.after is not None and refused.after["requested"] == 9000
    assert len(h.trail(h.p1, "TOKEN_CAP_RAISED")) == 1


def test_wg23_t03_another_build_may_raise_once_for_itself(h: CapsHarness) -> None:
    build = stopped(h)
    assert h.raise_(h.p1, "WORKFLOW", build, 1500).status_code == 200
    h.calls(h.p1, "build-s2-1", 2000)
    assert h.raise_(h.p1, "WORKFLOW", "build-s2-1", 4000).status_code == 200
    assert h.caps(h.p1).json()["caps"]["workflow_tokens"] == 4000


def test_wg23_a_raise_needs_a_reached_cap_a_higher_number_and_keeps_the_order(
    h: CapsHarness,
) -> None:
    assert h.put(h.p1, CAPS, '"0"').status_code == 200
    h.calls(h.p1, "build-s1-1", 100)
    problem(h.raise_(h.p1, "WORKFLOW", "build-s1-1", 5000), 409, "CONFLICT")  # not reached
    h.calls(h.p1, "build-s1-1", 1100)
    problem(h.raise_(h.p1, "WORKFLOW", "build-s1-1", 1000), 422, "VALIDATION_ERROR")  # not higher
    # a Sub-Skill cap may not climb above the Workflow cap
    problem(h.raise_(h.p1, "SUB_SKILL", "build-s1-1", 1500), 422, "VALIDATION_ERROR")
    assert h.trail(h.p1, "TOKEN_CAP_RAISED") == []
    # a refused raise did not use the one raise of this build
    assert h.raise_(h.p1, "WORKFLOW", "build-s1-1", 5000).status_code == 200


def test_wg23_the_sub_skill_cap_is_per_build(h: CapsHarness) -> None:
    assert h.put(h.p1, CAPS, '"0"').status_code == 200
    h.calls(h.p1, "build-s1-1", 300)
    h.calls(h.p1, "build-s2-1", 600)  # over the Sub-Skill cap of 500; the project has 900 in all
    store = SqlCallStore(h.sessions)
    budget = Budget(settings(), store)
    sc = scope(tenant_id=TENANT_A, project_id=uuid.UUID(h.p1), temporal_workflow_id="build-s2-1")
    with pytest.raises(BudgetExceeded, match="sub-skill build"):
        asyncio.run(budget.check_tokens(sc, "sbam-codegen"))
    other = scope(tenant_id=TENANT_A, project_id=uuid.UUID(h.p1), temporal_workflow_id="build-s1-1")
    asyncio.run(budget.check_tokens(other, "sbam-codegen"))  # 300 of 500: only an alert level
    assert h.raise_(h.p1, "SUB_SKILL", "build-s2-1", 700).status_code == 200


def test_wg23_each_project_is_checked_against_its_own_caps(h: CapsHarness) -> None:
    assert h.put(h.p1, CAPS, '"0"').status_code == 200  # p2 keeps the platform defaults
    h.calls(h.p1, "build-s1-1", 1200)
    h.calls(h.p2, "build-s2-1", 1200)
    budget = Budget(settings(), SqlCallStore(h.sessions))
    one = scope(tenant_id=TENANT_A, project_id=uuid.UUID(h.p1), temporal_workflow_id="build-s1-1")
    two = scope(tenant_id=TENANT_A, project_id=uuid.UUID(h.p2), temporal_workflow_id="build-s2-1")
    with pytest.raises(BudgetExceeded):
        asyncio.run(budget.check_tokens(one, "sbam-codegen"))
    asyncio.run(budget.check_tokens(two, "sbam-codegen"))  # 1200 of 200,000 / 2,000,000


def test_wg23_t05_the_meter_shows_the_level(h: CapsHarness) -> None:
    empty = h.meter(h.p1).json()
    assert [s["scope"] for s in empty["scopes"]] == ["SUB_SKILL", "WORKFLOW", "KNOWLEDGE"]
    assert all(s["used"] == 0 and s["level"] == "OK" for s in empty["scopes"])
    assert empty["scopes"][1]["cap"] == BudgetCaps().workflow_tokens
    assert h.put(h.p1, CAPS, '"0"').status_code == 200
    h.calls(h.p1, "build-s1-1", 450)  # sub-skill 90%, workflow 45%
    h.calls(h.p1, "k-1", 1000, alias="sbam-knowledge")  # knowledge 50%
    scopes = {s["scope"]: s for s in h.meter(h.p1).json()["scopes"]}
    assert scopes["SUB_SKILL"]["level"] == "SECOND_ALERT" and scopes["SUB_SKILL"]["used"] == 450
    assert scopes["WORKFLOW"]["level"] == "OK" and scopes["WORKFLOW"]["used"] == 450
    assert scopes["KNOWLEDGE"]["level"] == "FIRST_ALERT" and scopes["KNOWLEDGE"]["cap"] == 2000
    assert scopes["WORKFLOW"]["build_id"] == "build-s1-1"
    h.calls(h.p1, "build-s1-1", 600)
    assert {s["scope"]: s["level"] for s in h.meter(h.p1).json()["scopes"]}["WORKFLOW"] == "STOPPED"
    assert h.raise_(h.p1, "WORKFLOW", "build-s1-1", 9000).status_code == 200
    after = {s["scope"]: s for s in h.meter(h.p1).json()["scopes"]}
    assert after["WORKFLOW"]["level"] == "OK" and after["WORKFLOW"]["raised_once"] is True


def test_wg23_t04_another_tenant_or_project_is_a_404_on_every_route(h: CapsHarness) -> None:
    build = stopped(h)
    foreign = h.pb
    # tenant B's admin against tenant A's project
    problem(h.caps(h.p1, h.admin_b()), 404, "NOT_FOUND")
    problem(h.put(h.p1, CAPS, '"1"', h.admin_b()), 404, "NOT_FOUND")
    problem(
        h.call(
            "POST",
            f"/admin/projects/{h.p1}/token-budget/raise",
            h.admin_b(),
            json={"scope": "WORKFLOW", "build_id": build, "tokens": 9000},
        ),
        404,
        "NOT_FOUND",
    )
    # tenant A's admin against tenant B's project
    problem(h.caps(foreign), 404, "NOT_FOUND")
    problem(h.put(foreign, CAPS, '"0"'), 404, "NOT_FOUND")
    # an FDE of tenant A: not assigned to p2, and tenant B's project is invisible
    problem(h.meter(h.p2), 404, "NOT_FOUND")
    problem(h.meter(foreign), 404, "NOT_FOUND")
    problem(h.raise_(h.p2, "WORKFLOW", build, 9000), 404, "NOT_FOUND")
    problem(h.raise_(h.p1, "WORKFLOW", build, 9000, h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    # the build id of another project is not found either, and uses nothing up
    h.calls(h.p2, "build-other-1", 5000)
    problem(h.raise_(h.p1, "WORKFLOW", "build-other-1", 9000), 404, "NOT_FOUND")
    assert h.caps(h.p1).json()["caps"] == CAPS
    assert h.trail(h.p1, "TOKEN_CAP_RAISED") == []


def test_wg23_roles_the_fde_cannot_set_caps_and_the_admin_has_no_meter(h: CapsHarness) -> None:
    problem(h.caps(h.p1, h.fde()), 403, "WRONG_PORTAL")
    problem(h.put(h.p1, CAPS, '"0"', h.fde()), 403, "WRONG_PORTAL")
    problem(h.meter(h.p1, h.admin_a()), 403, "WRONG_PORTAL")
    assert h.caps(h.p1).json()["is_default"] is True
