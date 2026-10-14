"""E10.1 POST /projects/{project_id}/pipeline/start through the real app and auth chain (#48)."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runs_db import FakeClient, all_rows, memory_sessions
from sbam_api.core.auth import ROLE_FDE, CurrentUser, fde
from sbam_api.core.ids import uuid7
from sbam_api.main import create_app
from test_core_app import ALL_OK, make_settings

TENANT, PROJECT = uuid.uuid4(), uuid7()
START = f"/api/v1/projects/{PROJECT}/pipeline/start"


def fde_user(projects: frozenset[uuid.UUID] = frozenset({PROJECT})) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        keycloak_sub="sub",
        tenant_id=TENANT,
        roles=frozenset({ROLE_FDE}),
        assigned_project_ids=projects,
    )


def client(
    tmp_path: Path, temporal: object = None, user: CurrentUser | None = None
) -> tuple[TestClient, object]:
    app = create_app(make_settings(tmp_path), ALL_OK)
    app.state.sessions = memory_sessions()
    if temporal is not None:
        app.state.temporal = temporal
    if user is not None:
        app.dependency_overrides[fde] = lambda: user
    return TestClient(app), app


def test_e10_1_start_returns_202_run_with_pipeline_id(tmp_path: Path) -> None:
    fake = FakeClient()
    c, _ = client(tmp_path, fake, fde_user())
    doc = uuid.uuid4()
    res = c.post(START, json={"document_version_ids": [str(doc)]})
    assert res.status_code == 202
    run = res.json()["run"]
    assert run["temporal_workflow_id"] == f"pipeline-{PROJECT}"
    assert run["type"] == "ProjectPipeline" and run["current_stage"] == "ingest"
    call = fake.calls[0]
    assert call["workflow"] == "ProjectPipeline" and call["task_queue"] == "sbam-ingest"
    assert call["id_conflict_policy"].name == "FAIL"
    assert call["arg"].document_version_ids == [doc]
    assert call["arg"].actor_id  # the FDE, in whose name FDE events are applied


def test_e10_1_t03_duplicate_start_is_409_pipeline_running(tmp_path: Path) -> None:
    fake = FakeClient()
    c, app = client(tmp_path, fake, fde_user())
    assert c.post(START).status_code == 202
    res = c.post(START)
    assert res.status_code == 409
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "PIPELINE_RUNNING"
    assert len(all_rows(app.state.sessions)) == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "temporal", [FakeClient(down=True), None], ids=["start-fails", "no-server"]
)
def test_e10_1_temporal_down_is_503_and_nothing_recorded(tmp_path: Path, temporal: object) -> None:
    c, app = client(tmp_path, temporal, fde_user())
    res = c.post(START)
    assert res.status_code == 503 and res.json()["code"] == "ORCHESTRATOR_UNAVAILABLE"
    assert all_rows(app.state.sessions) == []  # type: ignore[attr-defined]


def test_e10_1_unassigned_project_is_404_and_no_token_is_401(tmp_path: Path) -> None:
    fake = FakeClient()
    c, _ = client(tmp_path, fake, fde_user(projects=frozenset()))
    assert c.post(START).status_code == 404
    anonymous, _ = client(tmp_path, FakeClient())
    assert anonymous.post(START).status_code == 401
    assert fake.calls == []
