"""E2.4 RBAC APIs: invite users, change roles and activation, assign FDEs to projects.

SQLite stands in for Postgres and a fake stands in for Keycloak's admin API, so nothing here needs
Docker. The live invite email and session revocation (T01 live, T08) are in
tests/integration/test_rbac_keycloak.py.
"""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import Auth, TokenVerifier, idp_unavailable
from sbam_api.db.session import make_sessionmaker
from sbam_api.identity.idp_sync import converge_pending
from sbam_api.identity.keycloak_admin import user_exists
from sbam_api.identity.models import Tenant, User
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.main import create_app
from sbam_api.projects.repository import SqlMemberships
from sbam_contracts.api import API_BASE
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_auth import (
    ADMIN_A,
    AUDIENCE,
    FDE_A1,
    ISSUER,
    TENANT_A,
    TENANT_B,
    FakeJwks,
    Realm,
    problem,
)
from test_core_app import ALL_OK, make_settings
from test_identity import Audit
from test_projects import ADMIN, ADMIN_B, sqlite_engine

FDE = {"realm_access": {"roles": ["sbam-fde"]}}


class Users:
    """Keycloak's admin API: records every call; `taken` emails answer 409, `down` answers 503."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.taken: set[str] = set()
        self.down = False
        self.fail: set[str] = set()  # methods that answer 503 while the others work
        self.seen: list[Callable[[], None]] = []  # run at the start of every call

    def _call(self, *call: str) -> None:
        for look in self.seen:
            look()
        if self.down or call[0] in self.fail:
            raise idp_unavailable()
        self.calls.append(call)

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        if self.down:
            raise idp_unavailable()
        if email in self.taken:  # one namespace for the whole realm, whichever tenant invited
            raise user_exists()
        self._call("invite", email, *roles)
        self.taken.add(email)
        return str(uuid.uuid4())

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None:
        self._call("set_roles", user_id, ",".join(before), ",".join(after))

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        self._call("set_enabled", user_id, str(enabled))

    def logout(self, user_id: str) -> None:
        self._call("logout", user_id)

    def delete(self, user_id: str) -> None:
        self.calls.append(("delete", user_id))


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.realm = Realm()
        self.audit = Audit()
        self.users = Users()
        engine = sqlite_engine()
        User.metadata.create_all(engine, tables=[User.metadata.tables["sbam.project_members"]])
        self.sessions: sessionmaker[Session] = make_sessionmaker(engine)
        auth = Auth(
            TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)),
            memberships=SqlMemberships(lambda: self.sessions),
            audit=self.audit,
            directory=SqlUserDirectory(lambda: self.sessions, self.audit),
            users=self.users,
        )
        app = create_app(make_settings(tmp_path), ALL_OK, auth)
        app.state.sessions = self.sessions
        self.client = TestClient(app)
        with self.sessions() as s:
            s.add_all([Tenant(id=TENANT_A, name="Tenant A"), Tenant(id=TENANT_B, name="Tenant B")])
            s.commit()
        # First requests mirror admin-a and fde-a1 into `users`.
        assert self.call("GET", "/me", self.admin_a()).status_code == 200
        assert self.call("GET", "/me", self.fde_a1()).status_code == 200

    def call(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        return self.client.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)

    def admin_a(self) -> str:
        return self.realm.token(ADMIN_A, **ADMIN)

    def admin_b(self) -> str:
        return self.realm.token(ADMIN_B, tenant_id=[str(TENANT_B)], **ADMIN)

    def fde_a1(self) -> str:
        return self.realm.token(FDE_A1, **FDE)

    def user(self, user_id: uuid.UUID) -> dict[str, Any]:
        res = self.call("GET", "/admin/users", self.admin_a())
        assert res.status_code == 200, res.text
        found: dict[str, Any] = next(u for u in res.json()["items"] if u["id"] == str(user_id))
        return found

    def patch(self, user_id: uuid.UUID, body: dict[str, Any], version: int = 1) -> Any:
        return self.call(
            "PATCH",
            f"/admin/users/{user_id}",
            self.admin_a(),
            headers={"if-match": f'"{version}"'},
            json=body,
        )

    def pending(self, user_id: uuid.UUID) -> datetime | None:
        with self.sessions() as s:
            row = s.get(User, user_id)
            assert row is not None
            return row.idp_sync_pending_at

    def relay(self, minutes: int) -> tuple[int, int]:
        return converge_pending(
            self.sessions, self.users, datetime.now(UTC) + timedelta(minutes=minutes)
        )

    def project(self) -> str:
        res = self.call("POST", "/admin/projects", self.admin_a(), json={"name": "EMS Quote Bot"})
        assert res.status_code == 201, res.text
        project_id: str = res.json()["id"]
        return project_id

    def assign(self, project_id: str, *user_ids: uuid.UUID) -> Any:
        return self.call(
            "PUT",
            f"/admin/projects/{project_id}/members",
            self.admin_a(),
            json={"user_ids": [str(u) for u in user_ids]},
        )

    def audit_rows(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


# --- Invite --------------------------------------------------------------------------------


def test_e2_4_t01_invite_fde(h: Harness) -> None:
    res = h.call(
        "POST",
        "/admin/users/invite",
        h.admin_a(),
        json={"email": " New@Acme.com ", "roles": ["sbam-fde"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "INVITED" and body["email"] == "new@acme.com"
    assert body["portal_roles"] == ["sbam-fde"] and res.headers["etag"] == '"1"'
    assert h.users.calls == [("invite", "new@acme.com", "sbam-fde")]
    [row] = h.audit_rows("USER_INVITED")
    assert row.entity_id == body["id"] and row.after == {
        "email": "new@acme.com",
        "roles": ["sbam-fde"],
    }


def test_e2_4_t02_duplicate_invite(h: Harness) -> None:
    body = {"email": "new@acme.com", "roles": ["sbam-fde"]}
    assert h.call("POST", "/admin/users/invite", h.admin_a(), json=body).status_code == 201
    problem(h.call("POST", "/admin/users/invite", h.admin_a(), json=body), 409, "USER_EXISTS")


def invite_as(h: Harness, token: str, email: str) -> Any:
    return h.call(
        "POST", "/admin/users/invite", token, json={"email": email, "roles": ["sbam-fde"]}
    )


def test_a05_t01_foreign_and_unknown_address_answer_identically(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "shared@acme.com").status_code == 201
    foreign = invite_as(h, h.admin_b(), "shared@acme.com")
    unknown = invite_as(h, h.admin_b(), "nobody@acme.com")
    assert foreign.status_code == unknown.status_code == 201
    assert set(foreign.json()) == set(unknown.json())
    assert foreign.headers["etag"] == unknown.headers["etag"]
    for res, email in ((foreign, "shared@acme.com"), (unknown, "nobody@acme.com")):
        body = res.json()
        assert body["email"] == email and body["status"] == "INVITED"
        assert body["portal_roles"] == ["sbam-fde"] and body["row_version"] == 1
        assert body["last_login_at"] is None and body["display_name"] is None


def test_a05_t02_own_tenant_duplicate_is_still_409_ignoring_case(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "new@acme.com").status_code == 201
    problem(invite_as(h, h.admin_a(), "new@acme.com"), 409, "USER_EXISTS")
    problem(invite_as(h, h.admin_a(), "NEW@ACME.COM"), 409, "USER_EXISTS")


def test_a05_t03_foreign_invite_creates_no_row_and_no_keycloak_side_effects(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "shared@acme.com").status_code == 201
    calls_before = list(h.users.calls)
    assert invite_as(h, h.admin_b(), "shared@acme.com").status_code == 201
    assert h.users.calls == calls_before  # no role grant, no org member, no delete
    with h.sessions() as s:
        rows = list(s.scalars(select(User).where(User.email == "shared@acme.com")))
    assert [r.tenant_id for r in rows] == [TENANT_A]


def test_a05_t04_foreign_invite_is_audited_as_skipped(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "shared@acme.com").status_code == 201
    assert invite_as(h, h.admin_b(), "shared@acme.com").status_code == 201
    [row] = h.audit_rows("USER_INVITE_SKIPPED")
    assert row.tenant_id == TENANT_B and row.entity_id is None
    assert row.after == {"email": "shared@acme.com", "roles": ["sbam-fde"]}
    assert len(h.audit_rows("USER_INVITED")) == 1  # only tenant A's real invite


def test_a05_t05_phantom_id_is_unknown_to_every_tenant(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "shared@acme.com").status_code == 201
    phantom = invite_as(h, h.admin_b(), "shared@acme.com").json()["id"]
    for token in (h.admin_a(), h.admin_b()):
        res = h.call(
            "PATCH",
            f"/admin/users/{phantom}",
            token,
            headers={"if-match": '"1"'},
            json={"active": False},
        )
        problem(res, 404, "NOT_FOUND")
    listing = h.call("GET", "/admin/users", h.admin_b()).json()["items"]
    assert phantom not in {u["id"] for u in listing}


def test_a05_t06_taken_in_keycloak_only_matches_foreign(h: Harness) -> None:
    h.users.taken.add("kc-only@acme.com")
    res = invite_as(h, h.admin_a(), "kc-only@acme.com")
    assert res.status_code == 201 and res.json()["status"] == "INVITED"
    assert len(h.audit_rows("USER_INVITE_SKIPPED")) == 1
    with h.sessions() as s:
        assert s.scalar(select(User).where(User.email == "kc-only@acme.com")) is None


def test_a05_t07_keycloak_down_answers_503_for_every_address(h: Harness) -> None:
    assert invite_as(h, h.admin_a(), "shared@acme.com").status_code == 201
    h.users.down = True
    for email in ("shared@acme.com", "nobody@acme.com"):
        problem(invite_as(h, h.admin_b(), email), 503, "IDP_UNAVAILABLE")


@pytest.mark.parametrize("roles", [["super-admin"], ["sbam-fde", "super-admin"], []])
def test_e2_4_t03_cannot_grant_super_admin(h: Harness, roles: list[str]) -> None:
    res = h.call(
        "POST", "/admin/users/invite", h.admin_a(), json={"email": "x@acme.com", "roles": roles}
    )
    body = problem(res, 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["roles"]
    assert h.users.calls == []


def test_e2_4_invite_keycloak_down_leaves_no_row(h: Harness) -> None:
    h.users.down = True
    body = {"email": "new@acme.com", "roles": ["sbam-fde"]}
    problem(h.call("POST", "/admin/users/invite", h.admin_a(), json=body), 503, "IDP_UNAVAILABLE")
    with h.sessions() as s:
        assert s.scalars(select(User).where(User.email == "new@acme.com")).first() is None


def test_e2_4_fde_cannot_invite(h: Harness) -> None:
    body = {"email": "new@acme.com", "roles": ["sbam-fde"]}
    problem(h.call("POST", "/admin/users/invite", h.fde_a1(), json=body), 403, "WRONG_PORTAL")


# --- Roles and activation --------------------------------------------------------------------


def test_e2_4_t04_last_admin_cannot_demote_self(h: Harness) -> None:
    res = h.patch(ADMIN_A, {"roles": ["sbam-fde"]})
    problem(res, 409, "LAST_ADMIN")
    assert h.user(ADMIN_A)["portal_roles"] == ["sbam-admin"]
    problem(h.patch(ADMIN_A, {"active": False}), 409, "LAST_ADMIN")
    assert h.users.calls == []


def test_e2_4_demote_allowed_when_another_admin_is_active(h: Harness) -> None:
    other = uuid.uuid4()
    assert h.call("GET", "/me", h.realm.token(other, **ADMIN)).status_code == 200
    res = h.patch(ADMIN_A, {"roles": ["sbam-fde"]})
    assert res.status_code == 200, res.text
    assert res.json()["portal_roles"] == ["sbam-fde"] and res.headers["etag"] == '"2"'
    sub = str(ADMIN_A)
    assert h.users.calls == [
        ("set_enabled", sub, "True"),  # convergence restates the row; Keycloak ignores a no-op
        ("set_roles", sub, "sbam-admin", "sbam-fde"),
        ("logout", sub),
    ]
    [row] = h.audit_rows("ROLE_CHANGED")
    assert row.before == {"roles": ["sbam-admin"]} and row.after == {"roles": ["sbam-fde"]}


def test_e2_4_t05_stale_etag(h: Harness) -> None:
    first = h.patch(FDE_A1, {"roles": ["sbam-admin", "sbam-fde"]})
    assert first.status_code == 200, first.text
    problem(h.patch(FDE_A1, {"roles": ["sbam-fde"]}), 412, "PRECONDITION_FAILED")
    res = h.call("PATCH", f"/admin/users/{FDE_A1}", h.admin_a(), json={"active": False})
    problem(res, 428, "PRECONDITION_REQUIRED")


def test_e2_4_t07_cross_tenant_user_is_404(h: Harness) -> None:
    res = h.call(
        "PATCH",
        f"/admin/users/{FDE_A1}",
        h.admin_b(),
        headers={"if-match": '"1"'},
        json={"active": False},
    )
    problem(res, 404, "NOT_FOUND")
    assert h.user(FDE_A1)["status"] == "ACTIVE"


def test_e2_4_patch_rejects_super_admin_role(h: Harness) -> None:
    body = problem(h.patch(FDE_A1, {"roles": ["super-admin"]}), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["roles"]


def test_e2_4_deactivate_disables_revokes_and_blocks(h: Harness) -> None:
    res = h.patch(FDE_A1, {"active": False})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "DEACTIVATED"
    sub = str(FDE_A1)
    assert h.users.calls == [("set_enabled", sub, "False"), ("logout", sub)]
    [row] = h.audit_rows("USER_DEACTIVATED")
    assert row.before == {"status": "ACTIVE"} and row.after == {"status": "DEACTIVATED"}
    # A still-unexpired access token is refused from now on.
    problem(h.call("GET", "/me", h.fde_a1()), 401, "UNAUTHENTICATED")
    res = h.patch(FDE_A1, {"active": True}, version=2)
    assert res.status_code == 200 and res.json()["status"] == "ACTIVE"
    assert h.audit_rows("USER_REACTIVATED")
    assert h.call("GET", "/me", h.fde_a1()).status_code == 200


def test_a13_t01_keycloak_down_keeps_the_saved_change_pending(h: Harness) -> None:
    h.users.down = True
    res = h.patch(FDE_A1, {"active": False})
    assert res.status_code == 200 and res.json()["status"] == "DEACTIVATED"
    assert res.json()["row_version"] == 2
    assert h.pending(FDE_A1) is not None
    problem(h.call("GET", "/me", h.fde_a1()), 401, "UNAUTHENTICATED")  # the DB already refuses


def test_a13_t02_failing_logout_no_longer_splits_keycloak_and_the_row(h: Harness) -> None:
    h.users.fail = {"logout"}
    assert h.patch(FDE_A1, {"active": False}).status_code == 200
    sub = str(FDE_A1)
    assert h.users.calls == [("set_enabled", sub, "False")]  # Keycloak took the change
    assert h.user(FDE_A1)["status"] == "DEACTIVATED"  # and so did the row
    assert h.pending(FDE_A1) is not None
    h.users.fail = set()
    assert h.relay(minutes=2) == (1, 0)
    assert h.users.calls[1:] == [("set_enabled", sub, "False"), ("logout", sub)]
    assert h.pending(FDE_A1) is None and h.user(FDE_A1)["row_version"] == 2


def test_a13_t03_relay_waits_for_the_request_that_is_still_converging(h: Harness) -> None:
    h.users.down = True
    assert h.patch(FDE_A1, {"roles": ["sbam-admin"]}).status_code == 200
    h.users.down = False
    assert h.relay(minutes=0) == (0, 0)
    assert h.pending(FDE_A1) is not None
    assert h.relay(minutes=2) == (1, 0)
    sub = str(FDE_A1)
    assert h.users.calls == [
        ("set_enabled", sub, "True"),
        ("set_roles", sub, "sbam-fde", "sbam-admin"),
        ("logout", sub),
    ]


def test_a13_t04_the_change_is_committed_before_keycloak_is_called(h: Harness) -> None:
    states: list[str] = []

    def look() -> None:  # another connection: only a commit is visible to it
        states.append(str(h.user(FDE_A1)["status"]))

    h.users.seen.append(look)
    assert h.patch(FDE_A1, {"active": False}).status_code == 200
    assert states and set(states) == {"DEACTIVATED"}


def test_a13_t05_a_failing_relay_keeps_the_mark_and_counts_it(h: Harness) -> None:
    h.users.down = True
    assert h.patch(FDE_A1, {"active": False}).status_code == 200
    assert h.relay(minutes=2) == (0, 1)
    assert h.pending(FDE_A1) is not None


def test_e2_4_old_token_does_not_write_old_roles_back(h: Harness) -> None:
    assert h.patch(FDE_A1, {"roles": ["sbam-admin", "sbam-fde"]}).status_code == 200
    assert h.call("GET", "/me", h.fde_a1()).status_code == 200  # token still says sbam-fde only
    assert h.user(FDE_A1)["portal_roles"] == ["sbam-admin", "sbam-fde"]


def test_e2_4_invited_user_becomes_active_on_first_login(h: Harness) -> None:
    res = h.call(
        "POST",
        "/admin/users/invite",
        h.admin_a(),
        json={"email": "new@acme.com", "roles": ["sbam-fde"]},
    )
    new_id = uuid.UUID(res.json()["id"])
    assert h.call("GET", "/me", h.realm.token(new_id, **FDE)).status_code == 200
    assert h.user(new_id)["status"] == "ACTIVE"


# --- Project members -------------------------------------------------------------------------


def test_e2_4_assign_fde_and_unassign(h: Harness) -> None:
    project = h.project()
    res = h.assign(project, FDE_A1)
    assert res.status_code == 200, res.text
    assert [m["id"] for m in res.json()["members"]] == [str(FDE_A1)]
    assert h.call("GET", f"/admin/projects/{project}/members", h.admin_a()).json() == res.json()
    mine = h.call("GET", "/projects", h.fde_a1()).json()["items"]
    assert [p["id"] for p in mine] == [project]
    [row] = h.audit_rows("PROJECT_MEMBERS_CHANGED")
    assert row.before == {"user_ids": []} and row.after == {"user_ids": [str(FDE_A1)]}

    assert h.assign(project).json()["members"] == []
    # The removed FDE's very next call no longer sees the project.
    assert h.call("GET", "/projects", h.fde_a1()).json()["items"] == []
    assert len(h.audit_rows("PROJECT_MEMBERS_CHANGED")) == 2


def test_e2_4_t06_assign_non_fde(h: Harness) -> None:
    project = h.project()
    body = problem(h.assign(project, ADMIN_A), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["user_ids.0"]


def test_e2_4_assign_deactivated_unknown_or_other_tenant_user(h: Harness) -> None:
    project = h.project()
    b_user = uuid.uuid4()
    assert h.call("GET", "/me", h.realm.token(b_user, tenant_id=[str(TENANT_B)])).status_code == 200
    assert h.patch(FDE_A1, {"active": False}).status_code == 200
    body = problem(h.assign(project, FDE_A1, uuid.uuid4(), b_user), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["user_ids.0", "user_ids.1", "user_ids.2"]


def test_e2_4_assign_to_archived_or_other_tenant_project(h: Harness) -> None:
    project = h.project()
    problem(h.call("GET", f"/admin/projects/{project}/members", h.admin_b()), 404, "NOT_FOUND")
    res = h.call("PUT", f"/admin/projects/{project}/members", h.admin_b(), json={"user_ids": []})
    problem(res, 404, "NOT_FOUND")
    assert h.call("POST", f"/admin/projects/{project}/archive", h.admin_a()).status_code == 200
    problem(h.assign(project, FDE_A1), 409, "PROJECT_ARCHIVED")


def test_e2_4_members_input_limits(h: Harness) -> None:
    project = h.project()
    body = problem(h.assign(project, FDE_A1, FDE_A1), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["user_ids"]
    too_many = [uuid.uuid4() for _ in range(51)]
    problem(h.assign(project, *too_many), 422, "VALIDATION_ERROR")


# --- User list: limit, cursor, filters (E2.6 list inputs) ------------------------------------


def _list(h: Harness, token: str | None = None, **params: str) -> Any:
    return h.call("GET", "/admin/users", token or h.admin_a(), params=params)


def _add_fdes(h: Harness, n: int) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(n)]
    for i in ids:
        assert h.call("GET", "/me", h.realm.token(i, **FDE)).status_code == 200
    return ids


def test_e2_6_list_pages_with_a_signed_cursor(h: Harness) -> None:
    added = _add_fdes(h, 5)  # tenant A: admin-a, fde-a1 and 5 more = 7
    seen: list[str] = []
    cursor: str | None = None
    sizes = []
    while True:
        params = {"limit": "3", **({"cursor": cursor} if cursor else {})}
        body = _list(h, **params).json()
        sizes.append(len(body["items"]))
        seen += [u["id"] for u in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sizes == [3, 3, 1]
    # Oldest first (ids are random Keycloak UUIDs, so this is creation order), no repeats.
    assert seen == [str(i) for i in (ADMIN_A, FDE_A1, *added)]
    everything = _list(h).json()  # default limit 25
    assert len(everything["items"]) == 7 and everything["next_cursor"] is None


@pytest.mark.parametrize("limit", ["0", "101", "x"])
def test_e2_6_list_limit_bounds(h: Harness, limit: str) -> None:
    body = problem(_list(h, limit=limit), 400, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["limit"]


def test_e2_6_list_cursor_is_tamper_proof_and_tenant_bound(h: Harness) -> None:
    _add_fdes(h, 2)
    cursor = _list(h, limit="1").json()["next_cursor"]
    assert cursor
    payload, mac = cursor.split(".")
    forged = f"{payload}.{'B' if mac[0] == 'A' else 'A'}{mac[1:]}"
    problem(_list(h, cursor=forged), 400, "INVALID_CURSOR")
    problem(_list(h, cursor="not-a-cursor"), 400, "INVALID_CURSOR")
    problem(_list(h, h.admin_b(), cursor=cursor), 400, "INVALID_CURSOR")  # tenant A's cursor


def test_e2_6_list_filters_by_role_and_active(h: Harness) -> None:
    fdes = _add_fdes(h, 2)
    admins = [u["id"] for u in _list(h, role="sbam-admin").json()["items"]]
    assert admins == [str(ADMIN_A)]
    assert h.patch(fdes[0], {"active": False}).status_code == 200
    active_fdes = {u["id"] for u in _list(h, role="sbam-fde", active="true").json()["items"]}
    assert active_fdes == {str(FDE_A1), str(fdes[1])}
    inactive = [u["id"] for u in _list(h, active="false").json()["items"]]
    assert inactive == [str(fdes[0])]
    problem(_list(h, role="super-admin"), 400, "VALIDATION_ERROR")
    problem(_list(h, active="maybe"), 400, "VALIDATION_ERROR")
    # Tenant B never sees tenant A's users, filters or not.
    assert str(FDE_A1) not in {u["id"] for u in _list(h, h.admin_b()).json()["items"]}
