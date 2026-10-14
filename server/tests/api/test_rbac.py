"""E2.4 RBAC APIs: invite users, change roles and activation, assign FDEs to projects.

SQLite stands in for Postgres and a fake stands in for Keycloak's admin API, so nothing here needs
Docker. The live invite email and session revocation (T01 live, T08) are in
tests/integration/test_rbac_keycloak.py.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import Auth, TokenVerifier, idp_unavailable
from sbam_api.db.session import make_sessionmaker
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

    def _call(self, *call: str) -> None:
        if self.down:
            raise idp_unavailable()
        self.calls.append(call)

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        if email in self.taken:
            raise user_exists()
        self._call("invite", email, *roles)
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


def test_e2_4_invite_email_of_another_tenant(h: Harness) -> None:
    body = {"email": "shared@acme.com", "roles": ["sbam-fde"]}
    assert h.call("POST", "/admin/users/invite", h.admin_a(), json=body).status_code == 201
    res = h.call("POST", "/admin/users/invite", h.admin_b(), json=body)
    problem(res, 409, "USER_EXISTS")  # the same answer as within the tenant: nothing leaks


def test_e2_4_invite_taken_in_keycloak_only(h: Harness) -> None:
    h.users.taken.add("kc-only@acme.com")
    body = {"email": "kc-only@acme.com", "roles": ["sbam-fde"]}
    problem(h.call("POST", "/admin/users/invite", h.admin_a(), json=body), 409, "USER_EXISTS")


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
    assert h.users.calls == [("set_roles", sub, "sbam-admin", "sbam-fde"), ("logout", sub)]
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


def test_e2_4_keycloak_down_changes_nothing(h: Harness) -> None:
    h.users.down = True
    problem(h.patch(FDE_A1, {"active": False}), 503, "IDP_UNAVAILABLE")
    user = h.user(FDE_A1)
    assert user["status"] == "ACTIVE" and user["row_version"] == 1


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
