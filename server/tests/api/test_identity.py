"""E2.3 identity API: tenants (super-admin), the users mirror and GET /me.

SQLite stands in for Postgres (schema `sbam` mapped away) and the Keycloak organizations are a fake,
so nothing here needs Docker. The real Postgres race (T06), Keycloak organizations (T01) and
Keycloak down (T07) are in tests/integration/test_identity.py.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sbam_api.core.auth import Auth, CurrentUser, TokenVerifier, idp_unavailable
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity import service
from sbam_api.identity.models import Tenant, User
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.main import create_app
from sbam_contracts.api import API_BASE
from sqlalchemy import Engine, pool
from sqlalchemy.exc import IntegrityError
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
from test_core_app import ALL_OK, log_lines, make_settings

ROOT = uuid.uuid4()
SUPER = {"tenant_id": None, "realm_access": {"roles": ["super-admin"]}}


class Orgs:
    """Fake Keycloak organizations: `down` = Keycloak unreachable."""

    def __init__(self) -> None:
        self.created: dict[str, tuple[uuid.UUID, str]] = {}
        self.deleted: list[str] = []
        self.down = False

    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        if self.down:
            raise idp_unavailable()
        org_id = f"org-{len(self.created) + 1}"
        self.created[org_id] = (tenant_id, name)
        return org_id

    def delete(self, org_id: str) -> None:
        self.deleted.append(org_id)


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.denials: list[str] = []

    def denied(self, *, actor: CurrentUser, route: str, target: str | None, reason: str) -> None:
        self.denials.append(reason)

    def event(
        self,
        *,
        actor: str,
        tenant_id: uuid.UUID | None,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            {"action": action, "entity_id": entity_id, "tenant_id": tenant_id, **(after or {})}
            | {f"before_{k}": v for k, v in (before or {}).items()}
        )

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


def sqlite_engine() -> Engine:
    engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    tables = Tenant.metadata.tables
    Tenant.metadata.create_all(engine, tables=[tables["sbam.tenants"], tables["sbam.users"]])
    return engine


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.realm = Realm()
        self.orgs = Orgs()
        self.audit = Audit()
        self.sessions: sessionmaker[Session] = make_sessionmaker(sqlite_engine())
        auth = Auth(
            TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)),
            audit=self.audit,
            directory=SqlUserDirectory(lambda: self.sessions, self.audit),
            orgs=self.orgs,
        )
        app = create_app(make_settings(tmp_path), ALL_OK, auth)
        app.state.sessions = self.sessions
        self.client = TestClient(app)
        self.add_tenant(TENANT_A, "Tenant A")
        self.add_tenant(TENANT_B, "Tenant B")

    def add_tenant(self, tenant_id: uuid.UUID, name: str, status: str = "ACTIVE") -> None:
        with self.sessions() as s:
            s.add(Tenant(id=tenant_id, name=name, status=status))
            s.commit()

    def call(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        return self.client.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)

    def root(self) -> str:
        return self.realm.token(ROOT, **SUPER)

    def users(self) -> list[User]:
        with self.sessions() as s:
            return list(s.query(User))

    def tenants(self) -> list[Tenant]:
        with self.sessions() as s:
            return list(s.query(Tenant))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


# --- Tenants -----------------------------------------------------------------------------------


def test_e2_3_t01_create_tenant(h: Harness) -> None:
    res = h.call("POST", "/admin/tenants", h.root(), json={"name": "Acme EMS"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Acme EMS" and body["status"] == "ACTIVE"
    assert res.headers["etag"] == '"1"'
    assert h.orgs.created[body["keycloak_org_id"]] == (uuid.UUID(body["id"]), "Acme EMS")
    assert "TENANT_CREATED" in h.audit.actions()
    listed = h.call("GET", "/admin/tenants", h.root()).json()["items"]
    assert body["id"] in [t["id"] for t in listed]


def test_e2_3_t02_duplicate_name_ignores_case_and_spaces(h: Harness) -> None:
    assert h.call("POST", "/admin/tenants", h.root(), json={"name": "Acme EMS"}).status_code == 201
    res = h.call("POST", "/admin/tenants", h.root(), json={"name": " acme ems "})
    problem(res, 409, "DUPLICATE_NAME")
    assert len(h.orgs.created) == 1  # Keycloak never called for the duplicate


@pytest.mark.parametrize("name", ["A", "x" * 121, "bad\x07name", "   "])
def test_e2_3_t03_invalid_name(h: Harness, name: str) -> None:
    body = problem(
        h.call("POST", "/admin/tenants", h.root(), json={"name": name}), 422, "VALIDATION_ERROR"
    )
    assert [e["path"] for e in body["errors"]] == ["name"]


def test_e2_3_t04_non_super_admin_is_refused(h: Harness) -> None:
    admin = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    problem(h.call("POST", "/admin/tenants", admin, json={"name": "Acme EMS"}), 403, "WRONG_PORTAL")
    problem(h.call("GET", "/admin/tenants", admin), 403, "WRONG_PORTAL")
    assert {t.name for t in h.tenants()} == {"Tenant A", "Tenant B"} and not h.orgs.created


def test_e2_3_t07_keycloak_down_leaves_no_tenant(h: Harness) -> None:
    h.orgs.down = True
    problem(
        h.call("POST", "/admin/tenants", h.root(), json={"name": "Acme EMS"}),
        503,
        "IDP_UNAVAILABLE",
    )
    assert "Acme EMS" not in {t.name for t in h.tenants()}


def test_e2_3_commit_failure_deletes_the_organization(h: Harness) -> None:
    with (
        h.sessions() as s,
        mock.patch.object(
            s, "commit", side_effect=IntegrityError("insert", {}, Exception("unique"))
        ),
    ):
        with pytest.raises(Exception) as exc:
            service.create_tenant(s, h.orgs, h.audit, "root", "Racing Co")
    assert getattr(exc.value, "code", None) == "DUPLICATE_NAME"
    assert h.orgs.deleted == list(h.orgs.created)  # the org created for the lost race is removed


def test_e2_3_patch_status_needs_if_match(h: Harness) -> None:
    path = f"/admin/tenants/{TENANT_A}"
    problem(
        h.call("PATCH", path, h.root(), json={"status": "SUSPENDED"}), 428, "PRECONDITION_REQUIRED"
    )
    stale = {"if-match": '"7"'}
    problem(
        h.call("PATCH", path, h.root(), json={"status": "SUSPENDED"}, headers=stale),
        412,
        "PRECONDITION_FAILED",
    )
    problem(
        h.call("PATCH", path, h.root(), json={"status": "GONE"}, headers={"if-match": '"1"'}),
        422,
        "VALIDATION_ERROR",
    )
    res = h.call("PATCH", path, h.root(), json={"status": "SUSPENDED"}, headers={"if-match": '"1"'})
    assert res.status_code == 200 and res.json()["status"] == "SUSPENDED"
    assert res.headers["etag"] == '"2"'
    unknown = f"/admin/tenants/{uuid.uuid4()}"
    problem(
        h.call("PATCH", unknown, h.root(), json={"status": "ACTIVE"}, headers={"if-match": '"1"'}),
        404,
        "NOT_FOUND",
    )


# --- The users mirror and /me --------------------------------------------------------------------


def test_e2_3_t05_first_request_mirrors_the_user(h: Harness) -> None:
    token = h.realm.token(FDE_A1, email="fde-a1@sbam.test", name="FDE A1", sid="s1")
    res = h.call("GET", "/me", token)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["keycloak_sub"] == str(FDE_A1)
    assert body["user"]["portal_roles"] == ["sbam-fde"]
    assert body["tenant"]["id"] == str(TENANT_A) and body["roles"] == ["sbam-fde"]
    assert body["assigned_projects"] == []
    [row] = h.users()
    assert row.tenant_id == TENANT_A and row.email == "fde-a1@sbam.test"
    assert h.audit.events[0]["action"] == "LOGIN" and h.audit.events[0]["first_login"]


def test_e2_3_t06_repeated_first_requests_make_one_row(h: Harness) -> None:
    token = h.realm.token(FDE_A1)
    assert all(h.call("GET", "/me", token).status_code == 200 for _ in range(3))
    assert len(h.users()) == 1
    assert h.audit.actions() == ["LOGIN"]  # later requests write nothing
    # The race path: a second INSERT for the same subject is a no-op, not an error.
    user = CurrentUser(
        user_id=FDE_A1, keycloak_sub=str(FDE_A1), tenant_id=TENANT_A, roles=frozenset()
    )
    with h.sessions() as s:
        assert SqlUserDirectory(lambda: h.sessions, h.audit)._insert(s, user) is False


def test_e2_3_t08_suspended_tenant_is_refused(h: Harness) -> None:
    token = h.realm.token(FDE_A1)
    assert h.call("GET", "/me", token).status_code == 200
    h.call(
        "PATCH",
        f"/admin/tenants/{TENANT_A}",
        h.root(),
        json={"status": "SUSPENDED"},
        headers={"if-match": '"1"'},
    )
    problem(h.call("GET", "/me", token), 403, "TENANT_SUSPENDED")


def test_e2_3_user_never_moves_to_another_tenant(h: Harness) -> None:
    assert h.call("GET", "/me", h.realm.token(FDE_A1)).status_code == 200
    moved = h.realm.token(FDE_A1, tenant_id=[str(TENANT_B)])
    problem(h.call("GET", "/me", moved), 403, "TENANT_MISMATCH")
    assert h.users()[0].tenant_id == TENANT_A
    assert h.audit.denials == ["tenant mismatch"]


def test_e2_3_unknown_tenant_is_refused(h: Harness) -> None:
    stranger = h.realm.token(uuid.uuid4(), tenant_id=[str(uuid.uuid4())])
    problem(h.call("GET", "/me", stranger), 403, "TENANT_MISMATCH")
    assert h.users() == [] and h.audit.denials == ["unknown tenant"]


def test_e2_3_email_change_is_mirrored_and_audited(h: Harness) -> None:
    h.call("GET", "/me", h.realm.token(FDE_A1, email="old@sbam.test", preferred_username="fde-a1"))
    assert h.users()[0].display_name == "fde-a1"  # Keycloak sends no `name` without a full name
    h.call("GET", "/me", h.realm.token(FDE_A1, email="new@sbam.test"))
    assert h.users()[0].email == "new@sbam.test"
    [change] = [e for e in h.audit.events if e["action"] == "USER_EMAIL_CHANGED"]
    assert change["before_email"] == "old@sbam.test" and change["email"] == "new@sbam.test"


def test_e2_3_a_new_login_is_audited_once(h: Harness) -> None:
    h.call("GET", "/me", h.realm.token(FDE_A1, sid="s1"))
    h.call("GET", "/me", h.realm.token(FDE_A1, sid="s1"))  # same session
    h.call("GET", "/me", h.realm.token(FDE_A1, sid="s2"))  # signed in again
    assert h.audit.actions() == ["LOGIN", "LOGIN"]
    assert h.users()[0].last_login_at is not None


def test_e2_3_super_admin_me_has_no_tenant(h: Harness) -> None:
    body = h.call("GET", "/me", h.root()).json()
    assert body == {"user": None, "tenant": None, "roles": ["super-admin"], "assigned_projects": []}
    assert h.users() == []


def test_e2_3_audit_events_reach_the_json_log(capsys: pytest.CaptureFixture[str]) -> None:
    from sbam_api.core.auth import LogSecurityAudit
    from sbam_api.core.logging import configure_logging

    configure_logging()
    LogSecurityAudit().event(
        actor="root",
        tenant_id=None,
        action="TENANT_CREATED",
        entity="tenant",
        entity_id="t1",
        after={"a": 1},
    )
    [line] = [x for x in log_lines(capsys.readouterr().out) if x["msg"] == "audit"]
    assert line["action"] == "TENANT_CREATED" and line["entity"] == "tenant:t1"
    assert line["actor"] == "root"
    assert line["detail"] == {"tenant_id": "None", "before": None, "after": {"a": 1}}
