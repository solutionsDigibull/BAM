"""The seeded two-tenant world behind the RBAC matrix and IDOR suite (E2.7).

Everything runs in-process: the real app, the real authorization chain and the real SQL
repositories on SQLite (schema `sbam` mapped away), with in-memory RSA keys for the JWKS, a fake
Keycloak admin API and a fake Temporal client. Nothing here needs Docker.

The world has two tenants, each with data in every table the API reads, so a 404 can only come from
authorization, never from absence:

    tenant A  admin-a, fde-a1 (assigned to project A), fde-a2 (assigned to nothing)
              projects A and A2, documents, one run per project, an exportable file
    tenant B  admin-b, fde-b1 (assigned to project B), project B, a document, a run
    platform  super-admin (no tenant)

`rbac_matrix.yaml` says what every role must get from every route; `World.request` sends it.
"""

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import jwt
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from registry_db import sample_registry
from runs_db import FakeClient
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.audit.security import AuditLogSecurityAudit
from sbam_api.core.auth import Auth, Jwks, TokenVerifier
from sbam_api.core.ids import uuid7
from sbam_api.core.settings import Settings, load_settings
from sbam_api.db.base import Base
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant, User
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.main import create_app
from sbam_api.masters.models import MasterEntry, MasterSnapshot
from sbam_api.projects.models import Project
from sbam_api.projects.repository import SqlMemberships
from sbam_api.registry.sync import sync as sync_registry
from sbam_api.storage import Kind, StorageScope, signed_url
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.api import API_BASE
from sbam_contracts.build import BuildStage, SubSkillBuildStatus
from sbam_contracts.knowledge import CoverageEntry, GraphEdge, GraphNode, SourceStatus
from sbam_contracts.okf import PartRef
from sbam_contracts.runs import RunState, SubjectType, build_workflow_id
from sbam_contracts.state_machines import DocumentVersionState, SubSkillState
from sbam_workers.knowledge import okf
from sqlalchemy import func, pool, select
from sqlalchemy.orm import Session, sessionmaker

ISSUER = "http://kc.test/realms/bullm"
AUDIENCE = "sbam-api"
HOOK_SECRET = "rbac-suite-hook-secret"  # noqa: S105 - a test value
SIGNING_KEY = "rbac-suite-signing-key-0123456789abcdef"
MATRIX_FILE = Path(__file__).with_name("rbac_matrix.yaml")
# The seven columns every route must answer for (E2.7). `anon` sends no Authorization header.
ROLES = (
    "anon",
    "super_admin",
    "admin_a",
    "admin_b",
    "fde_a_assigned",
    "fde_a_unassigned",
    "fde_b",
)
# Rows that record denials and notifications on purpose; every other table must not change.
AUDIT_TABLES = frozenset({AuditLog.__tablename__, NotificationOutbox.__tablename__})
PROBLEM_JSON = "application/problem+json"


# --- Tokens and Keycloak ---------------------------------------------------------------------


class Realm:
    """In-memory signing key plus the JWKS a fake Keycloak would serve. One per test session."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "kid-rbac"

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def token(
        self,
        sub: uuid.UUID,
        roles: list[str],
        tenant: uuid.UUID | None,
        *,
        exp_in: int = 300,
        **claims: Any,
    ) -> str:
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(sub),
            "iat": now,
            "exp": now + exp_in,
            "realm_access": {"roles": roles},
            "tenant_id": [str(tenant)] if tenant else None,
            "sid": uuid.uuid4().hex,
            **claims,
        }
        body = {k: v for k, v in body.items() if v is not None}
        return jwt.encode(body, self.key, "RS256", headers={"kid": self.kid})


class FakeJwks(Jwks):
    def __init__(self, realm: Realm) -> None:
        super().__init__("http://kc.test/certs")
        self.realm = realm

    def fetch_data(self) -> Any:
        data = self.realm.jwks()
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


class FakeKeycloak:
    """The Keycloak admin API (organizations and users). Records every call: a denied request
    must leave `calls` empty."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        self.calls.append(("create_org", str(tenant_id)))
        return uuid.uuid4().hex

    def delete(self, org_id: str) -> None:
        self.calls.append(("delete", org_id))

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        self.calls.append(("invite", email))
        return str(uuid.uuid4())

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None:
        self.calls.append(("set_roles", user_id))

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        self.calls.append(("set_enabled", user_id))

    def logout(self, user_id: str) -> None:
        self.calls.append(("logout", user_id))


_realm: Realm | None = None


def shared_realm() -> Realm:
    """Generating an RSA key takes ~100 ms, so the whole session shares one."""
    global _realm
    if _realm is None:
        _realm = Realm()
    return _realm


# --- The world -------------------------------------------------------------------------------


@dataclass
class Actor:
    id: uuid.UUID
    roles: list[str]
    tenant: uuid.UUID | None
    email: str

    def token(self, realm: Realm, **kwargs: Any) -> str:
        return realm.token(
            self.id,
            self.roles,
            self.tenant,
            email=self.email,
            preferred_username=self.email,
            **kwargs,
        )


@dataclass
class World:
    """One fresh app and database. Build one per test: allowed requests change the data."""

    tmp: Path
    realm: Realm = field(default_factory=shared_realm)

    def __post_init__(self) -> None:
        self.keycloak = FakeKeycloak()
        self.temporal = FakeClient()
        engine = make_engine(
            "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
        ).execution_options(schema_translate_map={"sbam": None})
        Base.metadata.create_all(engine)
        self.sessions: sessionmaker[Session] = make_sessionmaker(engine)
        self.tenant_a, self.tenant_b = uuid.uuid4(), uuid.uuid4()
        a, b = self.tenant_a, self.tenant_b
        self.actors = {
            "super_admin": Actor(uuid.uuid4(), ["super-admin"], None, "root@platform.test"),
            "admin_a": Actor(uuid.uuid4(), ["sbam-admin"], a, "ada@alpha-industries.test"),
            "admin_b": Actor(uuid.uuid4(), ["sbam-admin"], b, "bob@beta-works.test"),
            "fde_a_assigned": Actor(uuid.uuid4(), ["sbam-fde"], a, "fiona@alpha-industries.test"),
            "fde_a_unassigned": Actor(uuid.uuid4(), ["sbam-fde"], a, "felix@alpha-industries.test"),
            "fde_b": Actor(uuid.uuid4(), ["sbam-fde"], b, "fay@beta-works.test"),
        }
        audit = AuditLogSecurityAudit(lambda: self.sessions)
        auth = Auth(
            TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)),
            memberships=SqlMemberships(lambda: self.sessions),
            audit=audit,
            directory=SqlUserDirectory(lambda: self.sessions, audit),
            orgs=self.keycloak,
            users=self.keycloak,
        )
        self.app: FastAPI = create_app(self.settings(), _ALL_OK, auth)
        self.app.state.sessions = self.sessions
        self.app.state.temporal = self.temporal
        self.client = TestClient(self.app)
        self._seed()

    def settings(self) -> Settings:
        return load_settings(
            _env_file=None,
            database_url="postgresql://sbam@127.0.0.1:1/sbam",
            storage_root=self.tmp,
            temporal_host="127.0.0.1:1",
            keycloak_issuer=ISSUER,
            keycloak_audience=AUDIENCE,
            enable_ping=True,
            tusd_hook_secret=SecretStr(HOOK_SECRET),
            storage_signing_key=SecretStr(SIGNING_KEY),
        )

    # -- seeding ---------------------------------------------------------------------------
    def _seed(self) -> None:
        with self.sessions() as s:
            s.add_all(
                [
                    Tenant(id=self.tenant_a, name="Alpha Industries"),
                    Tenant(id=self.tenant_b, name="Beta Works"),
                ]
            )
            s.commit()
        for role in ("admin_a", "admin_b", "fde_a_assigned", "fde_a_unassigned", "fde_b"):
            self.call("GET", "/me", role)  # first request mirrors the user into `users`
        self.project_a = self._create_project("admin_a", "Alpha Quote Bot")
        self.project_a2 = self._create_project("admin_a", "Alpha Invoice Reader")
        self.project_b = self._create_project("admin_b", "Beta Torque Planner")
        self._assign("admin_a", self.project_a, "fde_a_assigned")
        self._assign("admin_b", self.project_b, "fde_b")
        with self.sessions() as s:
            self.runs: dict[str, uuid.UUID] = {}
            self.docs: dict[str, uuid.UUID] = {}
            for name, tenant, project in (
                ("a", self.tenant_a, self.project_a),
                ("a2", self.tenant_a, self.project_a2),
                ("b", self.tenant_b, self.project_b),
            ):
                doc = Document(tenant_id=tenant, project_id=project, title=f"spec-{name}.pdf")
                s.add(doc)
                s.flush()
                version = DocumentVersion(
                    tenant_id=tenant,
                    project_id=project,
                    document_id=doc.id,
                    storage_key=f"{tenant}/{project}/uploads/spec-{name}",
                    sha256=f"{name:0>64}",
                    size=1024,
                    mime="application/pdf",
                    state=DocumentVersionState.PARSED.value,
                )
                run = TemporalRun(
                    tenant_id=tenant,
                    project_id=project,
                    temporal_workflow_id=f"pipeline-{project}",
                    temporal_run_id=f"run-{name}",
                    type="ProjectPipeline",
                    task_queue="sbam-ingest",
                    subject_type=SubjectType.PROJECT.value,
                    subject_id=project,
                    state=RunState.RUNNING.value,
                    current_stage="ingest",
                )
                s.add_all([version, run])
                s.flush()
                self.docs[name], self.runs[name] = doc.id, run.id
            s.commit()
        self.workflow_a, self.sub_a = uuid.uuid4(), uuid.uuid4()
        self.temporal.builds[build_workflow_id(self.sub_a, 1)] = SubSkillBuildStatus(
            tenant_id=self.tenant_a,
            project_id=self.project_a,
            workflow_id=self.workflow_a,
            sub_skill_id=self.sub_a,
            build_no=1,
            state=SubSkillState.NEEDS_FDE_REVIEW,
            stage=BuildStage.WAIT_RETRY,
            waiting_for_retry=True,
        )
        sync_registry(self.sessions, sample_registry(), activate=True)
        self.master_a = self._create_master("admin_a")
        self.master_b = self._create_master("admin_b")
        with self.sessions() as s:  # a pinned snapshot of project A's masters (E3.2)
            snapshot = MasterSnapshot(
                tenant_id=self.tenant_a,
                project_id=self.project_a,
                entries=[],
                sha256="sha256:" + "0" * 64,
            )
            s.add(snapshot)
            s.commit()
            self.snapshot_a = snapshot.id
        stored = LocalDiskBackend(self.tmp).put(
            b"alpha export", StorageScope(self.tenant_a, self.project_a), Kind.EXPORTS, ""
        )
        self.file_key = stored.storage_key
        query = urlsplit(
            signed_url(SIGNING_KEY, self.file_key, TenantContext(self.tenant_a), 600)
        ).query
        self.file_query = dict(part.split("=", 1) for part in query.split("&"))
        self.kv_a = self._seed_knowledge()

    def _create_project(self, role: str, name: str) -> uuid.UUID:
        res = self.call("POST", "/admin/projects", role, json={"name": name})
        assert res.status_code == 201, res.text
        return uuid.UUID(res.json()["id"])

    def _create_master(self, role: str) -> uuid.UUID:
        body = {"payload": {"domain_rule": "RULE-BOM: applies to BOM. Policy: compare"}}
        res = self.call("POST", "/admin/masters/RULES", role, json=body)
        assert res.status_code == 201, res.text
        return uuid.UUID(res.json()["id"])

    def _assign(self, admin: str, project: uuid.UUID, fde: str) -> None:
        body = {"user_ids": [str(self.actors[fde].id)]}
        res = self.call("PUT", f"/admin/projects/{project}/members", admin, json=body)
        assert res.status_code == 200, res.text

    # -- sending ---------------------------------------------------------------------------
    def token(self, role: str, **kwargs: Any) -> str | None:
        return None if role == "anon" else self.actors[role].token(self.realm, **kwargs)

    def call(
        self,
        method: str,
        path: str,
        role: str,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        """`path` is relative to /api/v1, unless it starts with /api/ or /health."""
        url = path if path.startswith(("/api/", "/health")) else f"{API_BASE}{path}"
        bearer = token if token is not None else self.token(role)
        sent = {"authorization": f"Bearer {bearer}"} if bearer else {}
        return self.client.request(method, url, headers={**sent, **(headers or {})}, **kwargs)

    def send(self, role: str, spec: "Request", **overrides: str) -> Response:
        """The matrix request for `role`. `overrides` replaces named path parameters (used to
        send a random id where the spec has a real one)."""
        url, kwargs = spec.build(self, overrides)
        return self.call(spec.method, url, role, **kwargs)

    def _seed_knowledge(self) -> uuid.UUID:
        """A READY knowledge version of project A with a one-entity OKF bundle (E5.4 reads)."""
        storage = LocalDiskBackend(self.tmp)
        where = StorageScope(self.tenant_a, self.project_a)
        dvid = uuid.uuid4()
        text = "Alpha supplier Mouser."
        doc = okf.Doc(dvid, text, hashlib.sha256(text.encode()).hexdigest())
        nodes = [
            GraphNode(id="d", type="TextDocument", name=f"{dvid}_{'0' * 64}"),
            GraphNode(id="c", type="DocumentChunk"),
            GraphNode(id="n1", type="Entity", name="Mouser"),
        ]
        edges = [
            GraphEdge(source="c", target="d", label="is_part_of"),
            GraphEdge(source="c", target="n1", label="contains"),
        ]
        outcome = CoverageEntry(document_version_id=dvid, status=SourceStatus.ADDED)
        built = okf.build_parts([doc], [outcome], nodes, edges, b"")
        refs = {}
        for name, data in built.parts.items():
            stored = storage.put(data, where, Kind.OKF, "application/json")
            refs[name] = PartRef(storage_key=stored.storage_key, sha256=stored.sha256)
        kv = uuid7()
        bundle = okf.manifest(
            version_id=kv,
            project_id=self.project_a,
            number=1,
            docs=[doc],
            parts=refs,
            snapshot_id=None,
            masters_sha256=okf.sha256(b""),
        )
        stored = storage.put(bundle, where, Kind.OKF, "application/json")
        with self.sessions() as s:
            s.add(
                KnowledgeVersion(
                    id=kv,
                    tenant_id=self.tenant_a,
                    project_id=self.project_a,
                    version=1,
                    run_id=uuid7(),
                    cognee_dataset="d",
                    coverage={},
                    state="READY",
                    okf_bundle_key=stored.storage_key,
                    okf_sha256=stored.sha256,
                )
            )
            s.commit()
        return kv

    # -- placeholders (`@name` in the matrix) ----------------------------------------------
    def value(self, name: str) -> str:
        """What `@name` stands for in the matrix."""
        fixed = {
            "tenant_a": self.tenant_a,
            "tenant_b": self.tenant_b,
            "project_a": self.project_a,
            "project_a2": self.project_a2,
            "project_b": self.project_b,
            "run_a": self.runs["a"],
            "run_b": self.runs["b"],
            "workflow_a": self.workflow_a,
            "sub_a": self.sub_a,
            "doc_a": self.docs["a"],
            "file_key": self.file_key,
            "master_a": self.master_a,
            "master_b": self.master_b,
            "snapshot_a": self.snapshot_a,
            "kv_a": self.kv_a,
        }
        if name in fixed:
            return str(fixed[name])
        if name.startswith("user_"):
            return str(self.actors[name.removeprefix("user_")].id)
        if name.startswith("file_"):
            return self.file_query[name.removeprefix("file_")]
        if name.startswith("etag_"):
            return f'"{self.version(name.removeprefix("etag_"))}"'
        raise KeyError(f"unknown placeholder @{name}")

    def version(self, what: str) -> int:
        """row_version of `tenant_a`, `project_a`, `master_a` or `user_<role>`."""
        with self.sessions() as s:
            if what.startswith("user_"):
                row: Any = s.get(User, self.actors[what.removeprefix("user_")].id)
            elif what.startswith("tenant_"):
                row = s.get(Tenant, uuid.UUID(self.value(what)))
            elif what.startswith("master_"):
                row = s.get(MasterEntry, uuid.UUID(self.value(what)))
            else:
                row = s.get(Project, uuid.UUID(self.value(what)))
            assert row is not None
            return int(row.row_version)

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("@"):
            return self.value(value[1:])
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        return value

    # -- observing -------------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """Rows per business table. Audit tables are left out: a denial is meant to write there."""
        with self.sessions() as s:
            return {
                t.name: int(s.scalar(select(func.count()).select_from(t)) or 0)
                for t in Base.metadata.sorted_tables
                if t.name not in AUDIT_TABLES
            }

    def side_effects(self) -> tuple[dict[str, int], int, int]:
        """Everything a denied request must not change: rows, Temporal starts, Keycloak calls."""
        return self.counts(), len(self.temporal.calls), len(self.keycloak.calls)

    def tokens_of(self, *scopes: str) -> dict[str, str]:
        """Strings that must not reach someone outside the scope: label -> text. Scopes are
        `a`, `a2` and `b` (a tenant-A project, a second tenant-A project, tenant B) and `tenant_a`
        / `tenant_b` for tenant-level values (tenant id and name, users)."""
        out: dict[str, str] = {}
        names = {
            "a": ("project_a", "Alpha Quote Bot", "spec-a.pdf"),
            "a2": ("project_a2", "Alpha Invoice Reader", "spec-a2.pdf"),
            "b": ("project_b", "Beta Torque Planner", "spec-b.pdf"),
        }
        for scope in scopes:
            if scope in names:
                pid, title, doc = names[scope]
                out[f"{scope} project id"] = self.value(pid)
                out[f"{scope} project name"] = title
                out[f"{scope} document title"] = doc
                out[f"{scope} document id"] = str(self.docs[scope])
                out[f"{scope} run id"] = str(self.runs[scope])
            else:  # tenant_a / tenant_b
                tenant = scope.removeprefix("tenant_")
                out[f"{scope} id"] = self.value(scope)
                out[f"{scope} name"] = "Alpha Industries" if tenant == "a" else "Beta Works"
                for role, actor in self.actors.items():
                    if actor.tenant == uuid.UUID(self.value(scope)):
                        out[f"{role} id"], out[f"{role} email"] = str(actor.id), actor.email
        return out


# --- The matrix ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Expect:
    status: int
    code: str | None = None

    @classmethod
    def parse(cls, raw: object) -> "Expect":
        """`403 WRONG_PORTAL` or `200` (a bare YAML int is fine too)."""
        status, _, code = str(raw).partition(" ")
        return cls(int(status), code or None)


@dataclass(frozen=True)
class Request:
    method: str
    path: str  # the OpenAPI path template
    params: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, Any] = field(default_factory=dict)
    json: Any = None

    def build(self, world: World, overrides: dict[str, str]) -> tuple[str, dict[str, Any]]:
        values = {**world.resolve(self.params), **overrides}
        url = self.path
        for name, value in values.items():
            url = url.replace("{" + name + "}", quote(str(value), safe="/"))
        query = world.resolve(self.query)
        if query:
            url = f"{url}?{urlencode(query)}"
        kwargs: dict[str, Any] = {"headers": world.resolve(self.headers)}
        if self.json is not None:
            kwargs["json"] = world.resolve(self.json)
        return url, kwargs


@dataclass(frozen=True)
class Route:
    operation_id: str
    request: Request
    expect: dict[str, Expect]
    raw_expect: dict[str, Any]


def load_matrix(path: Path = MATRIX_FILE) -> dict[str, Route]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    routes: dict[str, Route] = {}
    for operation_id, entry in doc["routes"].items():
        req = entry["request"]
        routes[operation_id] = Route(
            operation_id,
            Request(
                method=req["method"],
                path=req["path"],
                params=req.get("params", {}),
                query=req.get("query", {}),
                headers=req.get("headers", {}),
                json=req.get("json"),
            ),
            {role: Expect.parse(v) for role, v in entry["expect"].items()},
            entry["expect"],
        )
    return routes


def inventory(app: FastAPI) -> dict[str, tuple[str, str]]:
    """operationId -> (METHOD, path) for every operation in the app's generated OpenAPI."""
    app.openapi_schema = None  # a route added after the first call must show up
    found: dict[str, tuple[str, str]] = {}
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            found[operation["operationId"]] = (method.upper(), path)
    return found


def coverage_problems(app: FastAPI, matrix: dict[str, Route]) -> list[str]:
    """Deny by default: every route needs a matrix entry with all seven role columns (E2.7).

    Each problem names the operationId, so a failing build says which route to add.
    """
    problems: list[str] = []
    routes = inventory(app)
    for operation_id, (method, path) in sorted(routes.items()):
        entry = matrix.get(operation_id)
        if entry is None:
            problems.append(f"{operation_id}: {method} {path} has no entry in rbac_matrix.yaml")
            continue
        if (entry.request.method, entry.request.path) != (method, path):
            problems.append(
                f"{operation_id}: matrix says {entry.request.method} {entry.request.path}, "
                f"the app serves {method} {path}"
            )
        for role in ROLES:
            if role not in entry.expect:
                problems.append(f"{operation_id}: no expectation for role {role}")
        for role in entry.expect.keys() - set(ROLES):
            problems.append(f"{operation_id}: unknown role column {role}")
        for param in _placeholders(path):
            if param not in entry.request.params:
                problems.append(f"{operation_id}: path parameter {{{param}}} has no @value")
    for operation_id in sorted(matrix.keys() - routes.keys()):
        problems.append(f"{operation_id}: in rbac_matrix.yaml but no longer served (stale entry)")
    return problems


def _placeholders(path: str) -> list[str]:
    return [p.split("}")[0] for p in path.split("{")[1:]]


_ALL_OK: dict[str, Callable[[], Any]] = {}


async def _ok() -> bool:
    return True


_ALL_OK = {n: _ok for n in ("db", "storage", "temporal", "keycloak_jwks")}
