"""E3.1 admin projects API: create, edit, archive, and the FDE's `/projects`.

SQLite stands in for Postgres (schema `sbam` mapped away), so nothing here needs Docker. T08's race
is decided by the unique index; the test skips the friendly pre-check to hit that path.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.auth import Auth, TokenVerifier
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.main import create_app
from sbam_api.projects import service
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.workflows.models import Workflow
from sbam_contracts.api import API_BASE
from sqlalchemy import Engine, pool, select
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

ADMIN_B = uuid.uuid4()
ADMIN = {"realm_access": {"roles": ["sbam-admin"]}}


class Memberships:
    def __init__(self) -> None:
        self.assigned: dict[uuid.UUID, set[uuid.UUID]] = {}

    def assigned_projects(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        return frozenset(self.assigned.get(user_id, set()))


def sqlite_engine() -> Engine:
    engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    t = Tenant.metadata.tables
    Tenant.metadata.create_all(
        engine,
        tables=[
            t["sbam.tenants"],
            t["sbam.users"],
            t["sbam.projects"],
            AuditLog.__table__,  # type: ignore[list-item]
            NotificationOutbox.__table__,  # type: ignore[list-item]
            Workflow.__table__,  # type: ignore[list-item]  # archive checks for running ones
        ],
    )
    return engine


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.realm = Realm()
        self.audit = Audit()
        self.members = Memberships()
        self.sessions: sessionmaker[Session] = make_sessionmaker(sqlite_engine())
        auth = Auth(
            TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)),
            memberships=self.members,
            audit=self.audit,
            directory=SqlUserDirectory(lambda: self.sessions, self.audit),
        )
        app = create_app(make_settings(tmp_path), ALL_OK, auth)
        app.state.sessions = self.sessions
        self.client = TestClient(app)
        with self.sessions() as s:
            s.add_all([Tenant(id=TENANT_A, name="Tenant A"), Tenant(id=TENANT_B, name="Tenant B")])
            s.commit()

    def call(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        return self.client.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)

    def admin_a(self) -> str:
        return self.realm.token(ADMIN_A, **ADMIN)

    def admin_b(self) -> str:
        return self.realm.token(ADMIN_B, tenant_id=[str(TENANT_B)], **ADMIN)

    def create(self, name: str = "EMS Quote Bot", **fields: Any) -> dict[str, Any]:
        res = self.call("POST", "/admin/projects", self.admin_a(), json={"name": name, **fields})
        assert res.status_code == 201, res.text
        body: dict[str, Any] = res.json()
        return body

    def set_state(self, project_id: str, state: str) -> None:
        with self.sessions() as s:
            row = s.get(Project, uuid.UUID(project_id))
            assert row is not None
            row.state = state
            s.commit()

    def projects(self) -> list[Project]:
        with self.sessions() as s:
            return list(s.scalars(select(Project)))

    def audit_actions(self, entity_id: str) -> list[str]:
        with self.sessions() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id))
            return [r.action for r in rows]


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e3_1_t01_create_project(h: Harness) -> None:
    res = h.call(
        "POST",
        "/admin/projects",
        h.admin_a(),
        json={"name": "EMS Quote Bot", "client_name": "Acme", "context_notes": "# Notes"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "ACTIVE" and body["pipeline_state"] == "IDLE"
    assert body["client_name"] == "Acme" and body["context_notes"] == "# Notes"
    assert body["tenant_id"] == str(TENANT_A) and body["row_version"] == 1
    assert res.headers["etag"] == '"1"'
    assert h.audit_actions(body["id"]) == ["create_project", "PROJECT_CREATED"]
    got = h.call("GET", f"/admin/projects/{body['id']}", h.admin_a())
    same = {k: v for k, v in got.json().items() if not k.endswith("_at")}  # SQLite drops the tz
    assert same == {k: v for k, v in body.items() if not k.endswith("_at")}
    assert got.headers["etag"] == '"1"'


def test_e3_1_tenant_comes_from_the_token_not_the_body(h: Harness) -> None:
    res = h.call(
        "POST", "/admin/projects", h.admin_a(), json={"name": "X", "tenant_id": str(TENANT_B)}
    )
    problem(res, 422, "VALIDATION_ERROR")  # unknown fields are refused, never trusted
    assert h.create("X")["tenant_id"] == str(TENANT_A)


def test_e3_1_t02_duplicate_name_ignores_case_and_spaces(h: Harness) -> None:
    h.create("EMS Quote Bot")
    res = h.call("POST", "/admin/projects", h.admin_a(), json={"name": " ems quote bot "})
    problem(res, 409, "DUPLICATE_NAME")
    assert len(h.projects()) == 1


def test_e3_1_same_name_in_two_tenants_is_allowed(h: Harness) -> None:
    h.create("EMS Quote Bot")
    res = h.call("POST", "/admin/projects", h.admin_b(), json={"name": "EMS Quote Bot"})
    assert res.status_code == 201, res.text


def test_e3_1_t03_name_boundary(h: Harness) -> None:
    assert h.create("x" * 120)["name"] == "x" * 120
    assert h.create("  Ünïcødé 名前  ")["name"] == "Ünïcødé 名前"
    for name in ["x" * 121, "", "   ", "bad\x07name"]:
        body = problem(
            h.call("POST", "/admin/projects", h.admin_a(), json={"name": name}),
            422,
            "VALIDATION_ERROR",
        )
        assert [e["path"] for e in body["errors"]] == ["name"]


def test_e3_1_field_limits(h: Harness) -> None:
    for field, size in (("client_name", 121), ("description", 2001), ("context_notes", 10001)):
        body = problem(
            h.call("POST", "/admin/projects", h.admin_a(), json={"name": "P", field: "x" * size}),
            422,
            "VALIDATION_ERROR",
        )
        assert [e["path"] for e in body["errors"]] == [field]


def test_e3_1_edit_project(h: Harness) -> None:
    p = h.create()
    res = h.call(
        "PATCH",
        f"/admin/projects/{p['id']}",
        h.admin_a(),
        headers={"if-match": '"1"'},
        json={"description": "Quotes", "client_name": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["description"] == "Quotes" and res.json()["name"] == "EMS Quote Bot"
    assert res.headers["etag"] == '"2"'
    assert h.audit_actions(p["id"])[-1] == "PROJECT_UPDATED"
    null_name = h.call(
        "PATCH",
        f"/admin/projects/{p['id']}",
        h.admin_a(),
        headers={"if-match": '"2"'},
        json={"name": None},
    )
    problem(null_name, 422, "VALIDATION_ERROR")


def test_e3_1_rename_to_a_taken_name(h: Harness) -> None:
    h.create("Alpha")
    p = h.create("Beta")
    res = h.call(
        "PATCH",
        f"/admin/projects/{p['id']}",
        h.admin_a(),
        headers={"if-match": '"1"'},
        json={"name": "ALPHA"},
    )
    problem(res, 409, "DUPLICATE_NAME")


def test_e3_1_t04_stale_if_match(h: Harness) -> None:
    p = h.create()
    path = f"/admin/projects/{p['id']}"
    ok = h.call("PATCH", path, h.admin_a(), headers={"if-match": '"1"'}, json={"description": "a"})
    assert ok.status_code == 200
    stale = h.call(
        "PATCH", path, h.admin_a(), headers={"if-match": '"1"'}, json={"description": "b"}
    )
    problem(stale, 412, "PRECONDITION_FAILED")
    assert h.projects()[0].description == "a"


def test_e3_1_t05_missing_if_match(h: Harness) -> None:
    p = h.create()
    res = h.call("PATCH", f"/admin/projects/{p['id']}", h.admin_a(), json={"description": "a"})
    problem(res, 428, "PRECONDITION_REQUIRED")


def test_e3_1_t06_archive_while_running(h: Harness) -> None:
    p = h.create()
    h.set_state(p["id"], "INGESTING")
    res = h.call("POST", f"/admin/projects/{p['id']}/archive", h.admin_a())
    problem(res, 409, "PIPELINE_RUNNING")
    assert h.projects()[0].status == "ACTIVE"


@pytest.mark.parametrize("state", sorted(service.BUSY_WORKFLOW))
def test_e3_1_archive_while_a_workflow_builds_or_certifies(h: Harness, state: str) -> None:
    p = h.create()
    h.set_state(p["id"], "WORKFLOW_CREATED")
    with h.sessions() as s:
        workflow = Workflow(
            tenant_id=TENANT_A,
            project_id=uuid.UUID(p["id"]),
            sow_version_id=uuid.uuid4(),
            major=1,
            version="1.0.0",
            name="Bot v1",
        )
        workflow.state = state
        s.add(workflow)
        s.commit()
    res = h.call("POST", f"/admin/projects/{p['id']}/archive", h.admin_a())
    problem(res, 409, "PIPELINE_RUNNING")
    assert h.projects()[0].status == "ACTIVE"


@pytest.mark.parametrize("state", sorted(service.ARCHIVABLE))
def test_e3_1_archive_when_idle_failed_or_waiting(h: Harness, state: str) -> None:
    p = h.create()
    h.set_state(p["id"], state)
    res = h.call("POST", f"/admin/projects/{p['id']}/archive", h.admin_a())
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "ARCHIVED" and res.json()["pipeline_state"] == state
    assert h.audit_actions(p["id"])[-1] == "PROJECT_ARCHIVED"


def test_e3_1_archived_project_is_read_only_and_hidden_from_the_fde(h: Harness) -> None:
    p = h.create()
    h.members.assigned[FDE_A1] = {uuid.UUID(p["id"])}
    fde = h.realm.token(FDE_A1)
    assert [x["id"] for x in h.call("GET", "/projects", fde).json()["items"]] == [p["id"]]
    archived = h.call("POST", f"/admin/projects/{p['id']}/archive", h.admin_a()).json()
    again = h.call("POST", f"/admin/projects/{p['id']}/archive", h.admin_a())
    assert again.status_code == 200 and again.json()["row_version"] == archived["row_version"]
    assert h.call("GET", "/projects", fde).json()["items"] == []
    res = h.call(
        "PATCH",
        f"/admin/projects/{p['id']}",
        h.admin_a(),
        headers={"if-match": f'"{archived["row_version"]}"'},
        json={"description": "late"},
    )
    problem(res, 409, "PROJECT_ARCHIVED")
    # The admin still sees it, archived.
    assert h.call("GET", "/admin/projects", h.admin_a()).json()["items"][0]["status"] == "ARCHIVED"


def test_e3_1_fde_sees_only_assigned_projects(h: Harness) -> None:
    mine, other = h.create("Mine"), h.create("Other")
    h.members.assigned[FDE_A1] = {uuid.UUID(mine["id"])}
    items = h.call("GET", "/projects", h.realm.token(FDE_A1)).json()["items"]
    assert [x["id"] for x in items] == [mine["id"]] and other["id"] != mine["id"]


def test_e3_1_t07_cross_tenant_is_404(h: Harness) -> None:
    p = h.create()
    for method, path, kwargs in (
        ("GET", f"/admin/projects/{p['id']}", {}),
        ("PATCH", f"/admin/projects/{p['id']}", {"headers": {"if-match": '"1"'}, "json": {}}),
        ("POST", f"/admin/projects/{p['id']}/archive", {}),
    ):
        problem(h.call(method, path, h.admin_b(), **kwargs), 404, "NOT_FOUND")
    assert h.call("GET", "/admin/projects", h.admin_b()).json()["items"] == []
    assert h.projects()[0].status == "ACTIVE"


def test_e3_1_fde_cannot_use_admin_routes(h: Harness) -> None:
    res = h.call("POST", "/admin/projects", h.realm.token(FDE_A1), json={"name": "X"})
    problem(res, 403, "WRONG_PORTAL")
    assert h.projects() == []


def test_e3_1_admin_cannot_use_the_fde_list(h: Harness) -> None:
    problem(h.call("GET", "/projects", h.admin_a()), 403, "WRONG_PORTAL")


def test_e3_1_t08_parallel_create_lost_race_is_409(h: Harness) -> None:
    h.create("EMS Quote Bot")
    # The second request passed the pre-check before the first committed: the index decides.
    with mock.patch.object(ProjectRepository, "name_taken", return_value=False):
        res = h.call("POST", "/admin/projects", h.admin_a(), json={"name": "ems quote bot"})
    problem(res, 409, "DUPLICATE_NAME")
    assert len(h.projects()) == 1
