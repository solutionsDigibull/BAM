"""E15.3-T02 / T03 tokens on the real auth chain: expired, tampered, wrong portal/audience (#137).

test_token_lifecycle.py (E2.7-T06) proves expiry and forgery on one route (`GET /projects`). These
hold the spec's answers on each portal's routes and check the problem+json shape, and add the
spec's edge case: a token issued to the `sbam-admin` client used on the FDE API is 403.

Keycloak (compose/keycloak/realm-bullm.json) gives both portal clients the same audience
(`sbam-api`) and names the client in `azp`; a token for any other audience is 401.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from httpx2 import Response
from rbac_world import PROBLEM_JSON, World


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    """Shared: every request here is refused, so none changes the world."""
    w = World(tmp_path_factory.mktemp("e15-3-tokens"))
    yield w
    w.client.close()


# (method, path relative to /api/v1 with World placeholders, role whose token it is)
ROUTES: list[tuple[str, str, str]] = [
    ("GET", "/me", "fde_a_assigned"),
    ("GET", "/projects", "fde_a_assigned"),
    ("GET", "/projects/{project_a}/pipeline", "fde_a_assigned"),
    ("POST", "/projects/{project_a}/pipeline/start", "fde_a_assigned"),
    ("GET", "/admin/projects", "admin_a"),
    ("GET", "/admin/projects/{project_a}", "admin_a"),
]
IDS = [f"{m} {p}" for m, p, _ in ROUTES]
# The FDE API as the sbam-admin portal would call it.
FDE_ROUTES = [r for r in ROUTES if r[1].startswith("/projects")]


def url(world: World, path: str) -> str:
    return path.format(project_a=world.project_a)


def call(world: World, method: str, path: str, token: str) -> Response:
    return world.call(method, url(world, path), "anon", token=token)


def token(world: World, role: str, **claims: Any) -> str:
    return world.actors[role].token(world.realm, **claims)


def unauthenticated(res: Response) -> None:
    assert res.status_code == 401, res.text
    assert res.headers["content-type"].startswith(PROBLEM_JSON)
    assert res.json()["code"] == "UNAUTHENTICATED"
    assert res.headers["www-authenticate"].startswith("Bearer")


def flip(segment: str) -> str:
    """One base64url character in the middle changed (the last one may be only padding bits)."""
    i = len(segment) // 2
    return segment[:i] + ("A" if segment[i] != "A" else "B") + segment[i + 1 :]


@pytest.mark.parametrize(("method", "path", "role"), ROUTES, ids=IDS)
def test_e15_3_control_valid_token_is_accepted(
    world: World, method: str, path: str, role: str
) -> None:
    """The refusals below come from the token, not the route: the same token unchanged works.
    (pipeline/start is a write: its control may start a run; any answer but 401/403/404.)"""
    res = call(world, method, path, token(world, role))

    assert res.status_code not in (401, 403, 404), res.text


# --- E15.3-T02: expired ---------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "role"), ROUTES, ids=IDS)
def test_e15_3_t02_expired_token_401(world: World, method: str, path: str, role: str) -> None:
    expired = token(world, role, exp=1_000_000_000)  # 2001: long past any leeway

    unauthenticated(call(world, method, path, expired))


# --- tampered signature ---------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "role"), ROUTES, ids=IDS)
def test_e15_3_tampered_signature_401(world: World, method: str, path: str, role: str) -> None:
    head, body, sig = token(world, role).split(".")

    unauthenticated(call(world, method, path, f"{head}.{body}.{flip(sig)}"))


@pytest.mark.parametrize(("method", "path", "role"), ROUTES, ids=IDS)
def test_e15_3_stripped_signature_401(world: World, method: str, path: str, role: str) -> None:
    head, body, _ = token(world, role).split(".")

    unauthenticated(call(world, method, path, f"{head}.{body}."))


# --- E15.3-T03: the sbam-admin portal's token on the FDE API --------------------------------


@pytest.mark.parametrize(
    ("method", "path", "role"), FDE_ROUTES, ids=[f"{m} {p}" for m, p, _ in FDE_ROUTES]
)
def test_e15_3_t03_audience_mismatch(world: World, method: str, path: str, role: str) -> None:
    """An admin's token from the sbam-admin client (aud sbam-api, azp sbam-admin): 403."""
    admin = token(world, "admin_a", azp="sbam-admin")
    before = world.side_effects()

    res = call(world, method, path, admin)

    assert res.status_code == 403, res.text
    assert res.headers["content-type"].startswith(PROBLEM_JSON)
    assert res.json()["code"] == "WRONG_PORTAL"
    assert world.side_effects() == before


@pytest.mark.parametrize("aud", ["sbam-admin", "sbam-fde", "account"])
def test_e15_3_t03_token_for_another_audience_401(world: World, aud: str) -> None:
    """A token whose audience is not sbam-api (safety check: wrong audience -> 401)."""
    other = token(world, "fde_a_assigned", aud=aud)

    unauthenticated(call(world, "GET", "/projects/{project_a}/pipeline", other))


def test_e15_3_t03_fde_user_token_from_the_admin_client_403(world: World) -> None:
    """Spec edge case: the portal is the token's client (`azp`), not only the user's role."""
    from_admin_client = token(world, "fde_a_assigned", azp="sbam-admin")
    before = world.side_effects()

    for method, path, _ in FDE_ROUTES:
        res = call(world, method, path, from_admin_client)

        assert res.status_code == 403, res.text
        assert res.headers["content-type"].startswith(PROBLEM_JSON)
        assert res.json()["code"] == "WRONG_PORTAL"
    assert world.side_effects() == before


def test_e15_3_t03_control_each_portal_client_works_on_its_own_api(world: World) -> None:
    """The azp check refuses only the admin client on the FDE API: the FDE client's token on the
    FDE API and the admin client's token on the admin API are still accepted."""
    fde = token(world, "fde_a_assigned", azp="sbam-fde")
    admin = token(world, "admin_a", azp="sbam-admin")

    assert call(world, "GET", "/projects/{project_a}/pipeline", fde).status_code == 200
    assert call(world, "GET", "/admin/projects", admin).status_code == 200
