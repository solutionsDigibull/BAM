"""E1.7 run endpoints through the real app and auth chain (#48): 202 run, polling, 401/404/503."""

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runs_db import FakeClient, add_project, all_rows, memory_sessions
from sbam_api.core.auth import ROLE_FDE, CurrentUser, fde
from sbam_api.core.ids import uuid7
from sbam_api.main import create_app
from test_core_app import ALL_OK, make_settings

TENANT_A, TENANT_B, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid7()  # project ids are UUIDv7


def fde_user(
    tenant: uuid.UUID, projects: frozenset[uuid.UUID] = frozenset({PROJECT})
) -> CurrentUser:
    """A verified FDE (the token part is covered by #48's tests); assignment is checked for real."""
    return CurrentUser(
        user_id=uuid.uuid4(),
        keycloak_sub="sub",
        tenant_id=tenant,
        roles=frozenset({ROLE_FDE}),
        assigned_project_ids=projects,
    )


PING = f"/api/v1/projects/{PROJECT}/runs/ping"

MakeApp = Callable[..., tuple[FastAPI, TestClient]]


@pytest.fixture
def make_app(tmp_path: Path) -> MakeApp:
    def build(
        tenant: uuid.UUID | None = TENANT_A, temporal: object = None, **settings: object
    ) -> tuple[FastAPI, TestClient]:
        app = create_app(make_settings(tmp_path, **{"enable_ping": True, **settings}), ALL_OK)
        app.state.sessions = memory_sessions()
        if tenant is not None:
            add_project(app.state.sessions, tenant, PROJECT)
        if temporal is not None:
            app.state.temporal = temporal
        if tenant is not None:
            app.dependency_overrides[fde] = lambda: fde_user(tenant)
        return app, TestClient(app)

    return build


def test_e1_7_ping_returns_202_run_and_poll_reads_the_mirror(make_app: MakeApp) -> None:
    fake = FakeClient()
    _, client = make_app(temporal=fake)
    res = client.post(f"{PING}?queue=sbam-llm", headers={"X-Correlation-ID": "cid-42"})
    assert res.status_code == 202
    run = res.json()["run"]
    assert run["state"] == "RUNNING" and run["current_stage"] == "ping"
    assert run["temporal_workflow_id"] == f"ping-sbam-llm-{PROJECT}"
    assert fake.calls[0]["task_queue"] == "sbam-llm"
    assert fake.calls[0]["arg"].correlation_id == "cid-42"  # correlation id reaches the workflow
    polled = client.get(f"/api/v1/projects/{PROJECT}/runs/{run['id']}")
    assert polled.status_code == 200 and polled.json() == run


def test_e1_7_t04_ping_twice_is_one_execution(make_app: MakeApp) -> None:
    fake = FakeClient()
    _, client = make_app(temporal=fake)
    first, second = client.post(PING), client.post(PING)
    assert first.json()["run"]["id"] == second.json()["run"]["id"]
    assert len(fake.executions) == 1


def test_e1_7_t06_cross_tenant_run_is_404(make_app: MakeApp) -> None:
    app, client = make_app(temporal=FakeClient())
    run_id = client.post(PING).json()["run"]["id"]
    app.dependency_overrides[fde] = lambda: fde_user(TENANT_B)  # same project id, other tenant
    res = client.get(f"/api/v1/projects/{PROJECT}/runs/{run_id}")
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"
    app.dependency_overrides[fde] = lambda: fde_user(TENANT_A, frozenset())  # not assigned
    assert client.get(f"/api/v1/projects/{PROJECT}/runs/{run_id}").status_code == 404
    app.dependency_overrides[fde] = lambda: fde_user(TENANT_A)
    other_project = client.get(f"/api/v1/projects/{uuid7()}/runs/{run_id}")
    assert other_project.status_code == 404
    unknown = client.get(f"/api/v1/projects/{PROJECT}/runs/{uuid.uuid4()}")
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "temporal", [FakeClient(down=True), None], ids=["start-fails", "no-server"]
)
def test_e1_7_t03_temporal_down_is_503_problem_and_no_mirror_row(
    make_app: MakeApp, temporal: object
) -> None:
    app, client = make_app(temporal=temporal)  # None: real client against 127.0.0.1:1
    res = client.post(PING)
    assert res.status_code == 503
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "ORCHESTRATOR_UNAVAILABLE"
    assert all_rows(app.state.sessions) == []


def test_e1_7_no_token_is_401(make_app: MakeApp) -> None:
    _, client = make_app(tenant=None, temporal=FakeClient())
    assert client.post(PING).status_code == 401
    assert client.get(f"/api/v1/projects/{PROJECT}/runs/{uuid.uuid4()}").status_code == 401


def test_e1_7_ping_is_off_unless_enabled(make_app: MakeApp) -> None:
    fake = FakeClient()
    _, client = make_app(temporal=fake, enable_ping=False)
    assert client.post(PING).status_code == 404
    assert fake.calls == []


def test_e1_7_unknown_queue_is_422(make_app: MakeApp) -> None:
    _, client = make_app(temporal=FakeClient())
    assert client.post(f"{PING}?queue=sbam-foo").status_code in (400, 422)
