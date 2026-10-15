"""E2.7 RBAC matrix: every route x every role, deny by default (#53).

The expectations live in `rbac_matrix.yaml`; this file proves the matrix covers the whole API and
then holds each cell to it, including what a denied request must not leave behind.
"""

import dataclasses
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Response
from rbac_world import (
    PROBLEM_JSON,
    ROLES,
    Request,
    Route,
    World,
    coverage_problems,
    load_matrix,
)
from sbam_api.core.ids import uuid7

MATRIX = load_matrix()
CASES = [pytest.param(op, role, id=f"{op}-{role}") for op in MATRIX for role in ROLES]
# What each role must never see in a response body: scopes of tokens_of() (rbac_world.py).
FOREIGN = {
    "admin_a": ("tenant_b", "b"),
    "admin_b": ("tenant_a", "a", "a2"),
    "fde_a_assigned": ("tenant_b", "b", "a2"),
    "fde_a_unassigned": ("tenant_b", "b", "a", "a2"),
    "fde_b": ("tenant_a", "a", "a2"),
}
ID_PARAMS = ("tenant_id", "user_id", "project_id", "run_id", "doc_id")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path)
    yield w
    w.client.close()


def sent_text(world: World, spec: Request) -> str:
    """Everything the request itself carries; the server may echo that back, nothing else."""
    url, kwargs = spec.build(world, {})
    return url + json.dumps(kwargs.get("json")) + json.dumps(kwargs["headers"])


def problem_code(res: Response) -> str | None:
    if res.status_code < 400:
        return None
    assert res.headers["content-type"].startswith(
        PROBLEM_JSON
    ), f"{res.status_code} is not problem+json: {res.headers['content-type']}"
    code = res.json()["code"]
    assert isinstance(code, str)
    return code


def leaks(world: World, role: str, res: Response, spec: Request) -> list[str]:
    """Foreign ids and names in the body. Errors may mention nothing; successes nothing foreign."""
    if res.status_code < 400:
        scopes = FOREIGN.get(role, ())
    else:
        scopes = ("tenant_a", "tenant_b", "a", "a2", "b")
    own = sent_text(world, spec)
    body = res.text
    found = []
    for scope in scopes:
        for label, text in world.tokens_of(scope).items():
            if text in body and text not in own:
                found.append(f"{label} ({text})")
    return found


# --- E2.7-T01 / T02: the matrix covers the API, deny by default -----------------------------


def test_e2_7_t01_matrix_complete_every_route_has_all_seven_role_expectations(
    world: World,
) -> None:
    assert coverage_problems(world.app, MATRIX) == []
    assert all(route.expect.keys() == set(ROLES) for route in MATRIX.values())
    assert len(ROLES) == 7


def test_e2_7_t02_new_route_without_entry_fails_and_names_the_operation_id(world: World) -> None:
    @world.app.get("/api/v1/dummy-not-in-matrix")
    def dummy_route() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    problems = coverage_problems(world.app, MATRIX)

    assert len(problems) == 1
    assert problems[0].startswith("dummy_route_api_v1_dummy_not_in_matrix_get:")
    assert "rbac_matrix.yaml" in problems[0]


def test_e2_7_t02_entry_missing_a_role_column_fails_and_names_route_and_role(
    world: World,
) -> None:
    operation_id = "get_run_api_v1_projects__project_id__runs__run_id__get"
    entry = MATRIX[operation_id]
    broken = {
        **MATRIX,
        operation_id: dataclasses.replace(
            entry, expect={role: e for role, e in entry.expect.items() if role != "fde_b"}
        ),
    }

    assert coverage_problems(world.app, broken) == [
        f"{operation_id}: no expectation for role fde_b"
    ]


def test_e2_7_t02_stale_or_mismatched_entries_fail(world: World) -> None:
    ghost = Route(
        "ghost_route", Request("GET", "/api/v1/ghost"), MATRIX["me_api_v1_me_get"].expect, {}
    )
    wrong = dataclasses.replace(MATRIX["me_api_v1_me_get"], request=Request("POST", "/api/v1/me"))
    problems = coverage_problems(
        world.app, {**MATRIX, "ghost_route": ghost, "me_api_v1_me_get": wrong}
    )

    assert any(p.startswith("ghost_route:") and "stale" in p for p in problems)
    assert any(p.startswith("me_api_v1_me_get:") and "POST" in p for p in problems)


# --- the matrix itself ----------------------------------------------------------------------


@pytest.mark.parametrize(("operation_id", "role"), CASES)
def test_e2_7_matrix_route_x_role(world: World, operation_id: str, role: str) -> None:
    route = MATRIX[operation_id]
    want = route.expect[role]
    before = world.side_effects()

    res = world.send(role, route.request)

    assert res.status_code == want.status, f"{role}: {res.status_code} {res.text}"
    assert problem_code(res) == want.code
    assert leaks(world, role, res, route.request) == [], f"{role} sees foreign data"
    if res.status_code >= 400 and route.request.method != "GET":
        assert world.side_effects() == before, "a denied request changed state"


# --- no existence oracle --------------------------------------------------------------------


def without_correlation(res: Response) -> dict[str, Any]:
    body = dict(res.json())
    body.pop("correlation_id")
    return {
        "status": res.status_code,
        "content_type": res.headers["content-type"],
        "www_authenticate": res.headers.get("www-authenticate"),
        "body": body,
    }


def random_ids(spec: Request) -> dict[str, str]:
    return {name: str(uuid7()) for name in spec.params if name in ID_PARAMS}


NOT_FOUND_CASES = [
    pytest.param(op, role, id=f"{op}-{role}")
    for op, route in MATRIX.items()
    for role in ROLES
    if (route.expect[role].status == 404 and random_ids(route.request))
]


@pytest.mark.parametrize(("operation_id", "role"), NOT_FOUND_CASES)
def test_e2_7_matrix_not_found_looks_like_a_random_id(
    world: World, operation_id: str, role: str
) -> None:
    """A foreign resource and one that does not exist answer identically (TD section 9)."""
    spec = MATRIX[operation_id].request

    real = world.send(role, spec)
    absent = world.send(role, spec, **random_ids(spec))

    assert real.status_code == 404
    assert without_correlation(real) == without_correlation(absent)
    assert uuid.UUID(real.json()["correlation_id"]) != uuid.UUID(absent.json()["correlation_id"])
