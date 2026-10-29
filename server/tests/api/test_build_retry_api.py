"""E10.2 POST /projects/{p}/workflows/{w}/sub-skills/{s}/retry through the real app and auth chain.

A fake Temporal client stands in for the SubSkillBuild child (the real workflow is exercised in
tests/temporal/test_sub_skill_build.py): it answers `describe`, `build_status` and records signals,
and, like the workflow, accepts a retry only while the build waits and only once per key.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from runs_db import add_project, memory_sessions
from sbam_api.core.auth import ROLE_FDE, CurrentUser, fde
from sbam_api.core.ids import uuid7
from sbam_api.main import create_app
from sbam_contracts.build import BuildStage, SubSkillBuildStatus
from sbam_contracts.runs import build_workflow_id
from sbam_contracts.state_machines import SubSkillState
from temporalio.service import RPCError, RPCStatusCode
from test_core_app import ALL_OK, make_settings

TENANT, PROJECT, WORKFLOW, SUB = uuid.uuid4(), uuid7(), uuid.uuid4(), uuid.uuid4()
URL = f"/api/v1/projects/{PROJECT}/workflows/{WORKFLOW}/sub-skills/{SUB}/retry"
BODY = {"reason": "Fixed the ISATVON field"}


def key(n: int = 1) -> dict[str, str]:
    return {"Idempotency-Key": f"retry-key-{n:04d}"}


def status(state: SubSkillState, *, waiting: bool, **kw: Any) -> SubSkillBuildStatus:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "workflow_id": WORKFLOW,
        "sub_skill_id": SUB,
        "build_no": 2,
        "state": state,
        "stage": BuildStage.WAIT_RETRY if waiting else BuildStage.AST_GATE,
        "waiting_for_retry": waiting,
        **kw,
    }
    return SubSkillBuildStatus(**values)


@dataclass
class FakeBuilds:
    """`builds` builds exist (build-<sub>-1 ... -<builds>); the newest answers `current`."""

    current: SubSkillBuildStatus | None
    builds: int = 2
    down: bool = False
    signals: list[tuple[str, str, Any]] = field(default_factory=list)

    def get_workflow_handle(self, workflow_id: str, **_: Any) -> "FakeBuildHandle":
        return FakeBuildHandle(self, workflow_id)


@dataclass
class FakeBuildHandle:
    owner: FakeBuilds
    id: str

    async def describe(self, **_: Any) -> None:
        if self.owner.down:
            raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")
        known = {build_workflow_id(SUB, n) for n in range(1, self.owner.builds + 1)}
        if self.id not in known:
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

    async def query(self, name: str, **_: Any) -> SubSkillBuildStatus:
        assert name == "build_status" and self.owner.current is not None
        return self.owner.current

    async def signal(self, name: str, payload: Any) -> None:
        owner = self.owner
        owner.signals.append((self.id, name, payload))
        assert owner.current is not None
        if owner.current.waiting_for_retry:  # what the workflow does with an accepted retry
            owner.current = owner.current.model_copy(
                update={
                    "waiting_for_retry": False,
                    "retry_keys": [*owner.current.retry_keys, payload.idempotency_key],
                }
            )


def fde_user(projects: frozenset[uuid.UUID] = frozenset({PROJECT})) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        keycloak_sub="sub",
        tenant_id=TENANT,
        roles=frozenset({ROLE_FDE}),
        assigned_project_ids=projects,
    )


def client(tmp_path: Path, temporal: object, user: CurrentUser | None = None) -> TestClient:
    app = create_app(make_settings(tmp_path), ALL_OK)
    app.state.temporal = temporal
    app.state.sessions = memory_sessions()
    add_project(app.state.sessions, TENANT, PROJECT)  # the archive guard reads it
    if user is not None:
        app.dependency_overrides[fde] = lambda: user
    return TestClient(app)


def test_e10_2_retry_in_review_is_202_and_signals_the_newest_build(tmp_path: Path) -> None:
    fake = FakeBuilds(status(SubSkillState.NEEDS_FDE_REVIEW, waiting=True))
    user = fde_user()
    res = client(tmp_path, fake, user).post(URL, json=BODY, headers=key())
    assert res.status_code == 202
    assert res.json() == {"sub_skill_id": str(SUB), "build_no": 2}
    [(workflow, name, payload)] = fake.signals
    assert workflow == build_workflow_id(SUB, 2) and name == "retry"
    assert payload.reason == BODY["reason"] and payload.actor_id == str(user.user_id)


def test_e10_2_t05_retry_while_building_is_409_not_in_review(tmp_path: Path) -> None:
    fake = FakeBuilds(status(SubSkillState.BUILDING, waiting=False))
    res = client(tmp_path, fake, fde_user()).post(URL, json=BODY, headers=key())
    assert res.status_code == 409
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "NOT_IN_REVIEW"
    assert fake.signals == []


def test_e10_2_t09_same_idempotency_key_is_the_same_202_and_one_signal(tmp_path: Path) -> None:
    fake = FakeBuilds(status(SubSkillState.NEEDS_FDE_REVIEW, waiting=True))
    c = client(tmp_path, fake, fde_user())
    first = c.post(URL, json=BODY, headers=key(7))
    second = c.post(URL, json=BODY, headers=key(7))
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(fake.signals) == 1
    other = c.post(URL, json=BODY, headers=key(8))  # a new key while it is BUILDING again
    assert other.status_code == 409 and other.json()["code"] == "NOT_IN_REVIEW"


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "short"}], ids=["missing", "invalid"])
def test_e10_2_retry_needs_a_valid_idempotency_key(tmp_path: Path, headers: dict[str, str]) -> None:
    fake = FakeBuilds(status(SubSkillState.NEEDS_FDE_REVIEW, waiting=True))
    res = client(tmp_path, fake, fde_user()).post(URL, json=BODY, headers=headers)
    assert res.status_code == 400 and res.json()["code"] == "VALIDATION_ERROR"
    assert fake.signals == []


@pytest.mark.parametrize("reason", ["", "x" * 501], ids=["empty", "too-long"])
def test_e10_2_retry_reason_is_1_to_500_characters(tmp_path: Path, reason: str) -> None:
    fake = FakeBuilds(status(SubSkillState.NEEDS_FDE_REVIEW, waiting=True))
    res = client(tmp_path, fake, fde_user()).post(URL, json={"reason": reason}, headers=key())
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_ERROR"
    assert fake.signals == []


def test_e10_2_retry_idor_and_auth(tmp_path: Path) -> None:
    waiting = status(SubSkillState.NEEDS_FDE_REVIEW, waiting=True)
    fake = FakeBuilds(waiting)
    # a project the FDE isn't assigned to, a malformed one: 404, nothing asked of Temporal
    assert (
        client(tmp_path, fake, fde_user(frozenset()))
        .post(URL, json=BODY, headers=key())
        .status_code
        == 404
    )
    bad = URL.replace(str(PROJECT), "not-a-uuid")
    assert client(tmp_path, fake, fde_user()).post(bad, json=BODY, headers=key()).status_code == 404
    # no token: 401
    assert client(tmp_path, fake).post(URL, json=BODY, headers=key()).status_code == 401
    # a Sub-Skill of another tenant / project / Workflow behind the same URL: 404
    for foreign in (
        waiting.model_copy(update={"tenant_id": uuid.uuid4()}),
        waiting.model_copy(update={"project_id": uuid7()}),
        waiting.model_copy(update={"workflow_id": uuid.uuid4()}),
    ):
        other = FakeBuilds(foreign)
        res = client(tmp_path, other, fde_user()).post(URL, json=BODY, headers=key())
        assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"
        assert other.signals == []
    assert fake.signals == []


def test_e10_2_retry_of_a_sub_skill_that_was_never_built_is_404(tmp_path: Path) -> None:
    fake = FakeBuilds(None, builds=0)
    res = client(tmp_path, fake, fde_user()).post(URL, json=BODY, headers=key())
    assert res.status_code == 404


def test_e10_2_retry_with_temporal_down_is_503(tmp_path: Path) -> None:
    fake = FakeBuilds(None, down=True)
    res = client(tmp_path, fake, fde_user()).post(URL, json=BODY, headers=key())
    assert res.status_code == 503 and res.json()["code"] == "ORCHESTRATOR_UNAVAILABLE"
