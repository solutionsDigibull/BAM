"""#720: the FDE's goal for a project, `GET` / `PUT /projects/{id}/goal`, through the real app and
auth chain. An assigned FDE reads and writes it (If-Match on the project's ETag, audited); anyone
else gets the same 404 as for a missing project; an archived project refuses the write (409)."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.projects.models import Project
from test_auth import FDE_A2, problem
from test_project_workspace import Harness

GOAL = "Build an AI agent that automates quoting for sales managers at SMB manufacturers in India"


def path(h: Harness, project: str | None = None) -> str:
    return f"/projects/{project or h.project}/goal"


def put(h: Harness, body: Any, if_match: str | None = '"1"', token: str | None = None) -> Any:
    headers = {} if if_match is None else {"If-Match": if_match}
    return h.call("PUT", path(h), token or h.fde(), headers=headers, json=body)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_720_t01_a_new_project_has_no_goal(h: Harness) -> None:
    res = h.call("GET", path(h), h.fde())
    assert res.status_code == 200, res.text
    assert res.json() == {"project_id": h.project, "goal": None}
    assert res.headers["etag"] == '"1"'


def test_720_t02_the_assigned_fde_sets_the_goal_and_it_is_audited(h: Harness) -> None:
    res = put(h, {"goal": f"  {GOAL}  "})
    assert res.status_code == 200, res.text
    assert res.json() == {"project_id": h.project, "goal": GOAL}  # trimmed
    assert res.headers["etag"] == '"2"'
    again = h.call("GET", path(h), h.fde())
    assert again.json()["goal"] == GOAL and again.headers["etag"] == '"2"'
    [row] = h.trail("project", "PROJECT_GOAL_SET")
    assert row.before == {"goal": None} and row.after == {"goal": GOAL}


def test_720_t03_null_clears_it_and_an_unchanged_goal_writes_nothing(h: Harness) -> None:
    assert put(h, {"goal": GOAL}).status_code == 200
    same = put(h, {"goal": GOAL}, if_match='"2"')
    assert same.status_code == 200 and same.headers["etag"] == '"2"'
    cleared = put(h, {"goal": None}, if_match='"2"')
    assert cleared.status_code == 200 and cleared.json()["goal"] is None
    assert len(h.trail("project", "PROJECT_GOAL_SET")) == 2


def test_720_t04_if_match_is_required_and_must_be_current(h: Harness) -> None:
    problem(put(h, {"goal": GOAL}, if_match=None), 428, "PRECONDITION_REQUIRED")
    problem(put(h, {"goal": GOAL}, if_match='"7"'), 412, "PRECONDITION_FAILED")
    assert h.call("GET", path(h), h.fde()).json()["goal"] is None


@pytest.mark.parametrize(
    "goal",
    ["", "   ", "x" * 301, "line one\nline two", "tab\there"],
    ids=["empty", "blank", "too-long", "newline", "tab"],
)
def test_720_t05_an_invalid_goal_is_422(h: Harness, goal: str) -> None:
    problem(put(h, {"goal": goal}), 422, "VALIDATION_ERROR")


def test_720_t06_extra_fields_are_422(h: Harness) -> None:
    problem(put(h, {"goal": GOAL, "name": "renamed"}), 422, "VALIDATION_ERROR")


def test_720_t07_other_tenant_or_unassigned_fde_gets_404(h: Harness) -> None:
    other = h.realm.token(FDE_A2)  # same tenant, not assigned to this project
    problem(h.call("GET", path(h), other), 404, "NOT_FOUND")
    problem(put(h, {"goal": GOAL}, token=other), 404, "NOT_FOUND")
    problem(h.call("GET", path(h, str(uuid.uuid4())), h.fde()), 404, "NOT_FOUND")
    problem(h.call("GET", path(h), h.admin_a()), 403, "WRONG_PORTAL")
    assert h.call("GET", path(h), h.fde()).json()["goal"] is None


def test_720_t08_an_archived_project_can_be_read_but_not_written(h: Harness) -> None:
    with h.sessions() as s:
        project = s.get(Project, uuid.UUID(h.project))
        assert project is not None
        project.status = "ARCHIVED"
        s.commit()
    assert h.call("GET", path(h), h.fde()).status_code == 200
    problem(put(h, {"goal": GOAL}), 409, "PROJECT_ARCHIVED")


def test_720_t09_the_goal_is_on_the_project_the_fde_lists(h: Harness) -> None:
    put(h, {"goal": GOAL})
    [item] = [
        p for p in h.call("GET", "/projects", h.fde()).json()["items"] if p["id"] == h.project
    ]
    assert item["goal"] == GOAL
