"""#802: `POST /projects/{id}/documents/relevance/rescore` starts `RelevanceRescore` for the project
(202 with the run); 409 without a goal; 503 when Temporal is down; the usual 404 / 403 for others.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_api.projects.models import Project
from sbam_contracts.documents import RELEVANCE_RESCORE, relevance_workflow_id
from temporalio.common import WorkflowIDConflictPolicy
from test_auth import FDE_A2, problem
from test_uploads import Harness

GOAL = "Build an AI agent that answers customer questions about our product catalogue"


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def path(h: Harness) -> str:
    return f"/projects/{h.project}/documents/relevance/rescore"


def set_goal(h: Harness, goal: str | None) -> None:
    with h.sessions() as s:
        project = s.get(Project, uuid.UUID(h.project))
        assert project is not None
        project.goal = goal
        s.commit()


def test_802_t30_without_a_goal_nothing_starts(h: Harness) -> None:
    problem(h.call("POST", path(h), h.fde()), 409, "CONFLICT")
    assert h.temporal.calls == []


def test_802_t31_with_a_goal_the_rescore_starts_and_replaces_a_running_one(h: Harness) -> None:
    set_goal(h, GOAL)
    res = h.call("POST", path(h), h.fde())
    assert res.status_code == 202, res.text
    assert res.json()["run"]["temporal_workflow_id"] == relevance_workflow_id(uuid.UUID(h.project))
    [call] = h.temporal.calls
    assert call["workflow"] == RELEVANCE_RESCORE
    assert call["id_conflict_policy"] == WorkflowIDConflictPolicy.TERMINATE_EXISTING
    assert str(call["arg"].project_id) == h.project


def test_802_t32_temporal_down_is_503(h: Harness) -> None:
    set_goal(h, GOAL)
    h.temporal.down = True
    problem(h.call("POST", path(h), h.fde()), 503, "ORCHESTRATOR_UNAVAILABLE")


def test_802_t33_unassigned_or_unknown_project_is_404_admin_is_403(h: Harness) -> None:
    set_goal(h, GOAL)
    problem(h.call("POST", path(h), h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    unknown = f"/projects/{uuid.uuid4()}/documents/relevance/rescore"
    problem(h.call("POST", unknown, h.fde()), 404, "NOT_FOUND")
    problem(h.call("POST", path(h), h.admin_a()), 403, "WRONG_PORTAL")
    assert h.temporal.calls == []
