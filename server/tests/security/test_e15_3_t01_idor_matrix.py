"""E15.3-T01 IDOR matrix: the OpenAPI decides what must be in rbac_matrix.yaml (#137).

test_rbac_matrix.py (E2.7) runs every route x role of rbac_matrix.yaml against the seeded
two-tenant world. This file does not run a second matrix; it holds that matrix to the IDOR rule
from the other side, starting from the app's generated OpenAPI:

* every /api/v1 operation with a path parameter is either tenant-scoped or named in `UNSCOPED`
  with its reason; a new route that is neither fails here;
* every tenant-scoped operation has a matrix entry whose request uses tenant A's seeded ids and
  whose cross-tenant column is `404 NOT_FOUND`:
      /api/v1/projects/{project_id}/...  fde_b (tenant B's FDE) -> 404
      /api/v1/admin/...                   admin_b (tenant B's admin) -> 404. "Its own rule": an
                                          FDE token there is 403 WRONG_PORTAL (E2.7-T05);
* the FDE denials leave an `access_denied` row in audit_log (the spec's "audit entries"), which
  the E2.7 runner does not check.
"""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from rbac_world import (
    _ALL_OK,
    AUDIENCE,
    HOOK_SECRET,
    ISSUER,
    SIGNING_KEY,
    World,
    inventory,
    load_matrix,
)
from sbam_api.audit.models import AuditLog
from sbam_api.audit.security import ACCESS_DENIED
from sbam_api.core.settings import load_settings
from sbam_api.main import create_app
from sqlalchemy import func, select

API = "/api/v1"
FDE_SCOPE = f"{API}/projects/{{project_id}}"
ADMIN_SCOPE = f"{API}/admin/"
CROSS_TENANT_ROLE = {"fde": "fde_b", "admin": "admin_b"}

# Routes under /api/v1 that take a path parameter but are not tenant-scoped by it. Each needs a
# reason; rbac_matrix.yaml still holds every role's answer for them.
UNSCOPED: dict[tuple[str, str], str] = {
    # Signed download: the HMAC signature over (key, tenant, exp) is the credential, not the
    # bearer token (test_idor.py E2.7-T08: a tampered link is 403 whatever the token).
    ("GET", f"{API}/files/{{key}}"): "signed URL, authorised by its signature",
    # Signed document link (#895): the HMAC over (tenant, project, document, exp) is the
    # credential, and the document is re-checked, tenant and project scoped, on every download
    # (test_895_document_access_api.py: other tenant / project / tampering are 404 / 403).
    ("GET", f"{API}/document-access/{{document_id}}"): "signed link, authorised by its signature",
    # A notification belongs to its recipient (filtered by user id), not to a tenant id in the
    # path; the matrix holds it to 404 for everyone else.
    ("POST", f"{API}/notifications/{{notification_id}}/read"): "user-scoped",
    # Published JSON Schemas: static platform data, the same for every tenant.
    ("GET", f"{API}/schemas/isatvon/{{version}}"): "static platform schema",
    ("GET", f"{API}/schemas/manifest/{{name}}"): "static platform schema",
    # The platform operator's API (super-admin only); a tenant admin is 403 WRONG_PORTAL.
    ("PATCH", f"{API}/admin/tenants/{{tenant_id}}"): "super-admin platform route",
    # master_type is an enum (RULES, SKILLS, ...), not an id: list and create act on the
    # caller's own tenant. Cross-tenant entries are .../{master_type}/{entry_id}, scoped below.
    ("GET", f"{API}/admin/masters/{{master_type}}"): "enum parameter, caller's own tenant",
    ("POST", f"{API}/admin/masters/{{master_type}}"): "enum parameter, caller's own tenant",
    # key is an enum (employees, customers, assets, systems): one row per (tenant, key), the
    # caller's own tenant (#782). Their RBAC rows are in rbac_matrix.yaml.
    ("GET", f"{API}/admin/master-lists/{{key}}"): "enum parameter, caller's own tenant",
    ("PUT", f"{API}/admin/master-lists/{{key}}"): "enum parameter, caller's own tenant",
}

# Placeholders (World.value) that name another tenant's or the caller's own data: a cross-tenant
# request built from them would not be an IDOR probe.
NOT_TENANT_A = ("@project_b", "@run_b", "@master_b", "@tenant_b", "@user_admin_b", "@user_fde_b")

MATRIX = load_matrix()


def _collection_app() -> FastAPI:
    """The app as `create_app` builds it, only to read its OpenAPI at collection time."""
    settings = load_settings(
        _env_file=None,
        database_url="postgresql://sbam@127.0.0.1:1/sbam",
        storage_root=Path(tempfile.mkdtemp(prefix="e15-3-openapi-")),
        temporal_host="127.0.0.1:1",
        keycloak_issuer=ISSUER,
        keycloak_audience=AUDIENCE,
        enable_ping=True,
        tusd_hook_secret=SecretStr(HOOK_SECRET),
        storage_signing_key=SecretStr(SIGNING_KEY),
    )
    return create_app(settings, _ALL_OK)


def parameterised(app: FastAPI) -> dict[str, tuple[str, str]]:
    """operationId -> (METHOD, path) of every /api/v1 operation with a path parameter."""
    return {
        op: (method, path)
        for op, (method, path) in inventory(app).items()
        if path.startswith(f"{API}/") and "{" in path
    }


def scope(method: str, path: str) -> str:
    if (method, path) in UNSCOPED:
        return "unscoped"
    if path == FDE_SCOPE or path.startswith(FDE_SCOPE + "/"):
        return "fde"
    if path.startswith(ADMIN_SCOPE):
        return "admin"
    return "unclassified"


PARAMETERISED = parameterised(_collection_app())
SCOPED = sorted(
    (op, m, p, scope(m, p))
    for op, (m, p) in PARAMETERISED.items()
    if scope(m, p) in CROSS_TENANT_ROLE
)
FDE_ROUTES = [(op, m, p) for op, m, p, s in SCOPED if s == "fde" and op in MATRIX]


def matrix_problems(operation_id: str, kind: str) -> list[str]:
    """Why the matrix entry of a tenant-scoped route is not an IDOR probe ([] if it is)."""
    entry = MATRIX.get(operation_id)
    if entry is None:
        return [f"{operation_id}: tenant-scoped route has no entry in rbac_matrix.yaml"]
    problems = []
    role = CROSS_TENANT_ROLE[kind]
    if entry.raw_expect.get(role) != "404 NOT_FOUND":
        problems.append(f"{operation_id}: {role} expects {entry.raw_expect.get(role)!r}, not 404")
    values = [str(v) for v in entry.request.params.values()]
    foreign = [v for v in values if v in NOT_TENANT_A]
    if foreign:
        problems.append(f"{operation_id}: path uses {foreign}, not tenant A's ids")
    if kind == "fde" and entry.request.params.get("project_id") != "@project_a":
        problems.append(f"{operation_id}: project_id is not @project_a")
    return problems


# --- the inventory --------------------------------------------------------------------------


def test_e15_3_t01_inventory_is_not_empty() -> None:
    """Guards against a vacuous pass: every case below comes from this inventory."""
    assert len(PARAMETERISED) >= 50
    assert sum(1 for *_, s in SCOPED if s == "fde") >= 40
    assert sum(1 for *_, s in SCOPED if s == "admin") >= 5
    assert any(p.endswith("/sow/versions/{version}/approve") for _, _, p in FDE_ROUTES)


def test_e15_3_t01_every_parameterised_route_is_classified() -> None:
    """A new route with a path parameter is tenant-scoped or listed in UNSCOPED, with a reason."""
    unclassified = sorted(
        f"{m} {p} ({op})" for op, (m, p) in PARAMETERISED.items() if scope(m, p) == "unclassified"
    )
    assert unclassified == [], "classify these routes (tenant-scoped prefix or UNSCOPED)"


def test_e15_3_t01_unscoped_allowlist_has_no_stale_entries() -> None:
    served = set(PARAMETERISED.values())
    assert sorted(k for k in UNSCOPED if k not in served) == []


def test_e15_3_t01_classifier_flags_a_new_unscoped_route() -> None:
    """The classification guard can fail: a route outside every scope is reported."""
    app = _collection_app()

    @app.get(f"{API}/widgets/{{widget_id}}")
    def widget(widget_id: str) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    found = parameterised(app)
    assert [(m, p) for m, p in found.values() if scope(m, p) == "unclassified"] == [
        ("GET", f"{API}/widgets/{{widget_id}}")
    ]


def test_e15_3_t01_matrix_check_flags_missing_and_weak_entries() -> None:
    """The per-route check can fail: no entry, a non-404 column, and foreign ids are reported."""
    op = "get_run_api_v1_projects__project_id__runs__run_id__get"
    assert matrix_problems("not_in_the_matrix", "fde") == [
        "not_in_the_matrix: tenant-scoped route has no entry in rbac_matrix.yaml"
    ]
    entry = MATRIX[op]
    weak = entry.__class__(
        op,
        entry.request.__class__("GET", entry.request.path, {"project_id": "@project_b"}),
        entry.expect,
        {**entry.raw_expect, "fde_b": "403 WRONG_PORTAL"},
    )
    MATRIX[op] = weak
    try:
        assert len(matrix_problems(op, "fde")) == 3
    finally:
        MATRIX[op] = entry


# --- every tenant-scoped route is an IDOR probe in rbac_matrix.yaml --------------------------


@pytest.mark.parametrize(
    ("operation_id", "kind"),
    [pytest.param(op, s, id=f"{m} {p}") for op, m, p, s in SCOPED],
)
def test_e15_3_t01_scoped_route_is_a_cross_tenant_404_in_the_matrix(
    operation_id: str, kind: str
) -> None:
    """test_rbac_matrix.py then sends that request as the other tenant and asserts the 404."""
    assert matrix_problems(operation_id, kind) == []


# --- the denial is audited ------------------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    """One world for the module: every request below is denied, so none changes it."""
    w = World(tmp_path_factory.mktemp("e15-3-t01"))
    yield w
    w.client.close()


def denied_rows(world: World) -> int:
    with world.sessions() as s:
        return int(
            s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == ACCESS_DENIED,
                    AuditLog.actor_id == str(world.actors["fde_b"].id),
                    AuditLog.entity_id == str(world.project_a),
                )
            )
            or 0
        )


@pytest.mark.parametrize(
    ("operation_id", "method", "path"),
    [pytest.param(*r, id=f"{r[1]} {r[2]}") for r in FDE_ROUTES],
)
def test_e15_3_t01_cross_tenant_fde_denial_writes_an_audit_entry(
    world: World, operation_id: str, method: str, path: str
) -> None:
    before_effects = world.side_effects()
    before = denied_rows(world)

    res = world.send("fde_b", MATRIX[operation_id].request)

    assert res.status_code == 404, f"{method} {path}: {res.status_code} {res.text[:300]}"
    assert world.side_effects() == before_effects, "a denied request changed state"
    assert denied_rows(world) == before + 1, "no access_denied audit entry for tenant A's id"
