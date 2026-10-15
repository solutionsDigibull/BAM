"""E10.1 POST /projects/{project_id}/pipeline/start through the real app and auth chain (#48).

#69 made the route check the project and its documents, so these run on a real project row (the
E4.1 harness) instead of stand-in tables. The workspace rules themselves are in
test_project_workspace.py.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.temporal.models import TemporalRun
from sqlalchemy import select
from test_auth import FDE_A2, problem
from test_project_workspace import Harness


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    harness.seed_versions(1)
    yield harness


def test_e10_1_start_returns_202_run_with_pipeline_id(h: Harness) -> None:
    res = h.start()
    assert res.status_code == 202
    run = res.json()["run"]
    assert run["temporal_workflow_id"] == f"pipeline-{h.project}"
    assert run["type"] == "ProjectPipeline" and run["current_stage"] == "ingest"
    call = h.temporal.calls[0]
    assert call["workflow"] == "ProjectPipeline" and call["task_queue"] == "sbam-ingest"
    assert call["id_conflict_policy"].name == "FAIL"
    assert call["arg"].actor_id  # the FDE, in whose name FDE events are applied


def test_e10_1_t03_duplicate_start_is_409_pipeline_running(h: Harness) -> None:
    assert h.start().status_code == 202
    res = h.start()
    assert res.status_code == 409
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "PIPELINE_RUNNING"
    assert h.count_runs() == 1


def test_e10_1_temporal_down_is_503_and_nothing_recorded(h: Harness) -> None:
    h.temporal.down = True
    problem(h.start(), 503, "TEMPORAL_UNAVAILABLE")  # #69: the code the E4.1 spec names
    with h.sessions() as session:
        assert list(session.scalars(select(TemporalRun))) == []


def test_e10_1_unassigned_project_is_404_and_no_token_is_401(h: Harness) -> None:
    problem(h.start(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    assert h.start(token="not-a-token").status_code == 401  # noqa: S106
    assert h.temporal.calls == []
