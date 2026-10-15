"""E2.7 IDOR cases: cross-tenant and cross-project access, no side effects, list filtering (#53).

The matrix (test_rbac_matrix.py) already proves the status of every route x role. These tests go
after the specifics the issue names: the body of a foreign 404 is the body of a random id, a denied
write leaves nothing behind, lists filter instead of 404, and the internal hook takes only its
secret.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Response
from rbac_world import HOOK_SECRET, World
from sbam_api.core.ids import uuid7

UPLOAD_BODY = {"files": [{"name": "spec.pdf", "size": 1024, "mime": "application/pdf"}]}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path)
    yield w
    w.client.close()


def shape(res: Response) -> dict[str, Any]:
    """The response minus the per-request correlation id."""
    body = dict(res.json())
    body.pop("correlation_id")
    return {"status": res.status_code, "type": res.headers["content-type"], "body": body}


# Each entry: (label, role, URL builder over the world, URL builder for the same shape with random
# ids). A documents GET route does not exist yet; when it lands add its row here and its entry to
# rbac_matrix.yaml (the matrix meta-test fails until the latter is done).
Path_ = Callable[[World], str]
FOREIGN_RUNS: list[tuple[str, str, Path_, Path_]] = [
    (
        "other tenant's project and run",
        "fde_b",
        lambda w: f"/projects/{w.project_a}/runs/{w.runs['a']}",
        lambda w: f"/projects/{uuid7()}/runs/{uuid7()}",
    ),
    (
        "other tenant's run through the caller's own project",
        "fde_b",
        lambda w: f"/projects/{w.project_b}/runs/{w.runs['a']}",
        lambda w: f"/projects/{w.project_b}/runs/{uuid7()}",
    ),
    (
        "unassigned project of the same tenant",
        "fde_a_assigned",
        lambda w: f"/projects/{w.project_a2}/runs/{w.runs['a2']}",
        lambda w: f"/projects/{uuid7()}/runs/{uuid7()}",
    ),
    (
        "same tenant, project the user is not assigned to at all",
        "fde_a_unassigned",
        lambda w: f"/projects/{w.project_a}/runs/{w.runs['a']}",
        lambda w: f"/projects/{uuid7()}/runs/{uuid7()}",
    ),
    (
        "run of another project through an assigned project",
        "fde_a_assigned",
        lambda w: f"/projects/{w.project_a}/runs/{w.runs['a2']}",
        lambda w: f"/projects/{w.project_a}/runs/{uuid7()}",
    ),
]


@pytest.mark.parametrize(
    ("label", "role", "real", "absent"), FOREIGN_RUNS, ids=[c[0] for c in FOREIGN_RUNS]
)
def test_e2_7_t03_cross_tenant_resource_404_same_shape_as_random_id(
    world: World, label: str, role: str, real: Path_, absent: Path_
) -> None:
    foreign = world.call("GET", real(world), role)
    random = world.call("GET", absent(world), role)

    assert foreign.status_code == 404, label
    assert shape(foreign) == shape(random)
    assert foreign.json()["code"] == "NOT_FOUND"
    # the body names nothing of tenant A's data, nothing of the caller's request either
    for text in world.tokens_of("a", "a2", "tenant_a").values():
        assert text not in foreign.text


def test_e2_7_t03_control_the_owner_does_see_the_run(world: World) -> None:
    """Without this, the 404 cases above could pass because the resource never existed."""
    res = world.call("GET", f"/projects/{world.project_a}/runs/{world.runs['a']}", "fde_a_assigned")

    assert res.status_code == 200
    assert res.json()["id"] == str(world.runs["a"])


def test_e2_7_t03_admin_project_of_other_tenant_404_same_shape_as_random_id(
    world: World,
) -> None:
    foreign = world.call("GET", f"/admin/projects/{world.project_a}", "admin_b")
    random = world.call("GET", f"/admin/projects/{uuid7()}", "admin_b")

    assert foreign.status_code == 404
    assert shape(foreign) == shape(random)


# --- E2.7-T04: unassigned project write ----------------------------------------------------


@pytest.mark.parametrize("role", ["fde_a_unassigned", "fde_b"])
def test_e2_7_t04_unassigned_project_upload_404_and_no_token_issued(
    world: World, role: str
) -> None:
    before = world.side_effects()

    res = world.call("POST", f"/projects/{world.project_a}/uploads", role, json=UPLOAD_BODY)

    assert res.status_code == 404
    assert "token" not in res.json()
    assert world.counts()["upload_reservations"] == 0
    assert world.side_effects() == before


def test_e2_7_t04_control_assigned_fde_does_get_a_token(world: World) -> None:
    res = world.call(
        "POST", f"/projects/{world.project_a}/uploads", "fde_a_assigned", json=UPLOAD_BODY
    )

    assert res.status_code == 201
    assert res.json()["token"]
    assert world.counts()["upload_reservations"] == 1


# --- E2.7-T05: admin token on an FDE write -------------------------------------------------


@pytest.mark.parametrize("role", ["admin_a", "admin_b", "super_admin"])
def test_e2_7_t05_admin_token_on_fde_write_403_and_no_run_created(world: World, role: str) -> None:
    before = world.side_effects()

    res = world.call("POST", f"/projects/{world.project_a}/pipeline/start", role)

    assert res.status_code == 403
    assert res.json()["code"] == "WRONG_PORTAL"
    assert world.counts()["temporal_runs"] == before[0]["temporal_runs"] == 3
    assert world.temporal.calls == []
    assert world.side_effects() == before


def test_e2_7_t05_control_assigned_fde_does_start_a_run(world: World) -> None:
    """Proves the counters in the denial cases can move."""
    res = world.call("POST", f"/projects/{world.project_a}/pipeline/start", "fde_a_assigned")

    assert res.status_code == 202
    assert world.counts()["temporal_runs"] == 4
    assert world.temporal.calls


# --- E2.7-T07: lists filter, they do not 404 -----------------------------------------------


def ids(res: Response) -> set[str]:
    assert res.status_code == 200, res.text
    return {item["id"] for item in res.json()["items"]}


def test_e2_7_t07_fde_project_list_returns_only_assigned_projects(world: World) -> None:
    mine = world.call("GET", "/projects", "fde_a_assigned")

    assert ids(mine) == {str(world.project_a)}
    for text in world.tokens_of("a2", "b", "tenant_b").values():
        assert text not in mine.text


def test_e2_7_t07_fde_without_assignments_gets_an_empty_list_not_404(world: World) -> None:
    res = world.call("GET", "/projects", "fde_a_unassigned")

    assert res.status_code == 200
    assert res.json()["items"] == []


def test_e2_7_t07_fde_of_other_tenant_sees_only_their_project(world: World) -> None:
    assert ids(world.call("GET", "/projects", "fde_b")) == {str(world.project_b)}


@pytest.mark.parametrize(
    ("role", "own", "foreign"),
    [("admin_a", ("a", "a2"), "b"), ("admin_b", ("b",), "a")],
)
def test_e2_7_t07_admin_project_list_is_tenant_scoped(
    world: World, role: str, own: tuple[str, ...], foreign: str
) -> None:
    res = world.call("GET", "/admin/projects", role)
    projects = {"a": world.project_a, "a2": world.project_a2, "b": world.project_b}

    assert ids(res) == {str(projects[k]) for k in own}
    for text in world.tokens_of(foreign, f"tenant_{foreign}").values():
        assert text not in res.text


def test_e2_7_t07_admin_user_list_holds_only_the_admins_tenant(world: World) -> None:
    mine = ids(world.call("GET", "/admin/users", "admin_a"))
    theirs = ids(world.call("GET", "/admin/users", "admin_b"))

    tenant_a = {str(world.actors[r].id) for r in ("admin_a", "fde_a_assigned", "fde_a_unassigned")}
    tenant_b = {str(world.actors[r].id) for r in ("admin_b", "fde_b")}
    assert mine == tenant_a
    assert theirs == tenant_b


def test_e2_7_t07_tenant_list_is_for_the_platform_operator_alone(world: World) -> None:
    assert len(ids(world.call("GET", "/admin/tenants", "super_admin"))) == 2
    for role in ("admin_a", "fde_b"):
        assert world.call("GET", "/admin/tenants", role).status_code == 403


# --- E2.7-T08: the internal hook ------------------------------------------------------------


@pytest.mark.parametrize("role", ["fde_a_assigned", "admin_a", "super_admin", "anon"])
def test_e2_7_t08_tusd_hook_with_user_token_and_no_secret_is_401(world: World, role: str) -> None:
    res = world.call("POST", "/internal/tusd/hooks", role, json={"Type": "pre-create"})

    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"
    assert world.counts()["upload_reservations"] == 0


def test_e2_7_t08_tusd_hook_with_user_token_and_wrong_secret_is_401(world: World) -> None:
    res = world.call(
        "POST",
        "/internal/tusd/hooks",
        "fde_a_assigned",
        headers={"x-tusd-hook-secret": HOOK_SECRET[:-1] + "!"},
        json={"Type": "pre-create"},
    )

    assert res.status_code == 401


def test_e2_7_t08_tusd_hook_accepts_the_secret_without_any_user_token(world: World) -> None:
    res = world.call(
        "POST",
        "/internal/tusd/hooks",
        "anon",
        headers={"x-tusd-hook-secret": HOOK_SECRET},
        json={"Type": "unrecognised-event"},
    )

    assert res.status_code == 200


@pytest.mark.parametrize("tamper", ["sig", "exp"])
def test_e2_7_t08_signed_download_with_tampered_link_is_403_whatever_the_token(
    world: World, tamper: str
) -> None:
    query = dict(world.file_query)
    query[tamper] = "1" if tamper == "exp" else query["sig"][:-2] + "00"

    res = world.call("GET", f"/files/{world.file_key}", "fde_b", params=query)

    assert res.status_code == 403
    assert res.json()["code"] == "FORBIDDEN"
    assert "alpha export" not in res.text
