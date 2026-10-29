"""A-26 (#452): a write on an archived project is 409 PROJECT_ARCHIVED on every project-scoped route
(ISATVON, workflows, builds, SOW, knowledge, ingest, pipeline, schema.sql), and reads still work.
The enumerating test fails for any project-scoped write route that skips the shared guard."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sbam_api.main import discover_routers
from sbam_api.projects.guard import active_project
from sbam_api.projects.models import Project
from test_auth import FDE_A2, problem
from test_isatvon_edit_api import Harness as EditHarness
from test_isatvon_edit_api import other_skill, regenerate
from test_isatvon_validate_api import sample

WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# POSTs that write nothing: the suffix of the path and why it may skip the guard.
READ_ONLY_POSTS = {
    "/isatvon/validate": "revalidates the Sub-Skills and returns a report; stores nothing",
}


def has_guard(dependant: Dependant) -> bool:
    return any(d.call is active_project or has_guard(d) for d in dependant.dependencies)


def project_writes() -> list[tuple[str, str, APIRoute]]:
    """Every write route on `/projects/{project_id}/...`, read from the routers before mounting
    (as `main.unguarded_routes` does: the mounted app keeps them behind a private wrapper)."""
    found = []
    for router in discover_routers():
        for route in router.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith(
                "/projects/{project_id}"
            ):
                continue  # the admin's own routes (archive, rename, members) are not FDE writes
            for method in sorted((route.methods or set()) & WRITES):
                found.append((method, route.path, route))
    return found


def test_a26_t01_every_project_scoped_write_route_has_the_archive_guard() -> None:
    writes = project_writes()
    assert len(writes) >= 15, "the route walk found too few writes; the filter is wrong"
    missing = [
        f"{method} {path}"
        for method, path, route in writes
        if not has_guard(route.dependant)
        and not any(path.endswith(suffix) for suffix in READ_ONLY_POSTS)
    ]
    assert missing == [], (
        "project-scoped write routes without `active_project` (use the `Project` type of "
        f"`sbam_api.projects.guard`, or list a write-free POST in READ_ONLY_POSTS): {missing}"
    )


def test_a26_t02_the_read_only_posts_are_real_routes() -> None:
    paths = [path for _, path, _ in project_writes()]
    for suffix in READ_ONLY_POSTS:
        assert any(p.endswith(suffix) for p in paths), f"{suffix} is no longer a route; drop it"


# --- behaviour ----------------------------------------------------------------------------------


@pytest.fixture
def h(tmp_path: Path) -> Iterator[EditHarness]:
    yield EditHarness(tmp_path)


def archive(h: EditHarness) -> None:
    with h.sessions() as s:
        project = s.get(Project, uuid.UUID(h.project))
        assert project is not None
        project.status = "ARCHIVED"
        s.commit()


def test_a26_t03_the_audits_calls_are_409_on_an_archived_project(h: EditHarness) -> None:
    first = h.add_sub_skill(sample(), order=1)
    second = h.add_sub_skill(other_skill(2), order=2)
    etag = h.etag_of(first)
    archive(h)
    rename = h.call(
        "PATCH",
        f"/projects/{h.project}/workflows/{h.wf}",
        h.fde(),
        headers={"If-Match": '"1"'},
        json={"name": "Renamed"},
    )
    problem(rename, 409, "PROJECT_ARCHIVED")
    problem(h.call("DELETE", h.sub_path(first), h.fde()), 409, "PROJECT_ARCHIVED")
    problem(h.patch(second, {"order": 1}), 409, "PROJECT_ARCHIVED")
    macro = h.call(
        "PATCH",
        f"/projects/{h.project}/workflows/{h.wf}/macro-skills/{h.macro}",
        h.fde(),
        headers={"If-Match": '"1"'},
        json={"name": "Renamed"},
    )
    problem(macro, 409, "PROJECT_ARCHIVED")
    problem(regenerate(h, second, "sub-skills"), 409, "PROJECT_ARCHIVED")
    problem(regenerate(h, h.macro, "macro-skills"), 409, "PROJECT_ARCHIVED")
    assert h.temporal.calls == []  # no billed run was started
    assert h.orders() == {first: 1, second: 2} and h.etag_of(first) == etag  # nothing changed


def test_a26_t04_sow_approve_and_new_version_are_409_too(h: EditHarness) -> None:
    archive(h)
    base = f"/projects/{h.project}/sow"
    approve = h.call("POST", f"{base}/versions/1/approve", h.fde(), json={"sha256": "0" * 64})
    problem(approve, 409, "PROJECT_ARCHIVED")
    problem(h.call("POST", f"{base}/new-version", h.fde(), json={}), 409, "PROJECT_ARCHIVED")


def test_a26_t05_reads_and_the_report_still_work(h: EditHarness) -> None:
    sub = h.add_sub_skill(sample())
    archive(h)
    base = f"/projects/{h.project}/workflows"
    assert h.call("GET", base, h.fde()).status_code == 200
    assert h.call("GET", h.sub_path(sub, "/edits"), h.fde()).status_code == 200
    assert h.validate().status_code == 200  # a POST, but it only reports


def test_a26_t06_an_unassigned_caller_gets_404_not_409(h: EditHarness) -> None:
    sub = h.add_sub_skill(sample())
    archive(h)
    other: Any = h.realm.token(FDE_A2)
    problem(h.call("DELETE", h.sub_path(sub), other), 404, "NOT_FOUND")
    problem(
        h.call("PATCH", f"/projects/{h.project}/workflows/{h.wf}", other, json={}), 404, "NOT_FOUND"
    )
