"""E3.7 Filetype table through the real app and auth chain: everyone reads, nobody edits."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_auth import problem
from test_projects import Harness

SUPER_ADMIN = {"realm_access": {"roles": ["super-admin"]}}


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e3_7_t01_fde_lists_43_filetypes_with_config(h: Harness) -> None:
    res = h.call("GET", "/admin/filetypes", h.realm.token())  # fde-A1
    assert res.status_code == 200, res.text
    rows = res.json()
    assert [r["row_label"] for r in rows] == [f"Filetype #{n}" for n in range(1, 44)]
    first = rows[0]
    assert (first["file_format"], first["tier"], first["mvp_enabled"], first["loader"]) == (
        ".cpp",
        1,
        True,
        "source-code",
    )
    assert first["default_route"] == "Agents"
    dwg = next(r for r in rows if r["file_format"] == ".dwg")
    assert (dwg["mvp_enabled"], dwg["loader"]) == (False, None)


@pytest.mark.parametrize("who", ["admin", "super_admin"])
def test_e3_7_admins_read_the_same_table(h: Harness, who: str) -> None:
    token = h.admin_a() if who == "admin" else h.realm.token(uuid.uuid4(), **SUPER_ADMIN)
    res = h.call("GET", "/admin/filetypes", token)
    assert res.status_code == 200 and len(res.json()) == 43


def test_a15_a_signed_in_account_without_a_portal_role_cannot_read_the_table(h: Harness) -> None:
    """Platform data, but not for any valid token: a role-less account is the wrong portal."""
    token = h.realm.token(uuid.uuid4(), realm_access={"roles": ["offline_access"]})
    problem(h.call("GET", "/admin/filetypes", token), 403, "WRONG_PORTAL")


def test_e3_7_anonymous_is_401(h: Harness) -> None:
    problem(h.client.get("/api/v1/admin/filetypes"), 401, "UNAUTHENTICATED")


def test_e3_7_t02_fde_edit_attempt_is_403(h: Harness) -> None:
    """Every write on the Filetype master is refused to an FDE (wrong portal)."""
    version = f"/admin/masters/FILETYPE/{uuid.uuid4()}/versions"
    res = h.call(
        "POST", version, h.realm.token(), json={"payload": {}}, headers={"if-match": '"1"'}
    )
    problem(res, 403, "WRONG_PORTAL")
    res = h.call("POST", "/admin/masters/FILETYPE", h.realm.token(), json={"payload": {}})
    problem(res, 403, "WRONG_PORTAL")


def test_e3_7_t03_admin_writing_a_filetype_is_filetype_frozen(h: Harness) -> None:
    version = f"/admin/masters/FILETYPE/{uuid.uuid4()}/versions"
    res = h.call("POST", version, h.admin_a(), json={"payload": {}}, headers={"if-match": '"1"'})
    problem(res, 403, "FILETYPE_FROZEN")
    problem(
        h.call("POST", "/admin/masters/FILETYPE", h.admin_a(), json={"payload": {}}),
        403,
        "FILETYPE_FROZEN",
    )
