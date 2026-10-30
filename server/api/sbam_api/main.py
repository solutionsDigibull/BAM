"""SBAM API app factory. Run: `uvicorn sbam_api.main:create_app --factory`.

Feature modules are never registered here: each `sbam_api.<module>` that exposes `router` is
discovered and mounted under /api/v1.
"""

import importlib
import pkgutil
from collections.abc import Mapping
from types import ModuleType

from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sbam_contracts.api import API_BASE

import sbam_api
from sbam_api.audit.security import AuditLogSecurityAudit
from sbam_api.core.auth import POLICIES, Auth, Jwks, NoOrgs, NoUserAdmin, TokenVerifier
from sbam_api.core.errors import install_error_handlers
from sbam_api.core.health import Check, default_checks, health_router
from sbam_api.core.logging import configure_logging
from sbam_api.core.middleware import BodyLimitMiddleware, CorrelationIdMiddleware
from sbam_api.core.ratelimit import AuditThrottle, RateLimiter
from sbam_api.core.settings import Settings, jwks_url, keycloak_realm_url, load_settings
from sbam_api.core.telemetry import configure_telemetry
from sbam_api.db.session import app_sessions
from sbam_api.identity.keycloak_admin import KeycloakOrgs, KeycloakUsers
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.projects.repository import SqlMemberships

_NOT_FEATURE_MODULES = {"core", "main"}


def discover_routers(package: ModuleType = sbam_api) -> list[APIRouter]:
    routers = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name in _NOT_FEATURE_MODULES or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            routers.append(router)
    return routers


def unguarded_routes(routers: list[APIRouter]) -> list[str]:
    """Routes with no policy dependency (core.auth.POLICIES) anywhere in their chain.

    Checked on the routers before mounting: a route's dependant already includes its router-level
    dependencies, and the mounted app keeps routers behind a private wrapper.
    """

    def guarded(dependant: Dependant) -> bool:
        return any(d.call in POLICIES or guarded(d) for d in dependant.dependencies)

    return [
        f"{sorted(route.methods or ())} {API_BASE}{route.path}"
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute) and not guarded(route.dependant)
    ]


def default_auth(app: FastAPI, settings: Settings) -> Auth:
    """Production wiring: Keycloak JWKS, the users mirror (#49), Keycloak organizations and
    users, and project memberships (#50)."""
    # Denials and identity events go to audit_log (#41, ADR 0017); one sessionmaker per app.
    audit = AuditLogSecurityAudit(
        lambda: app_sessions(app.state), AuditThrottle(settings.audit_denied_per_minute)
    )
    secret = settings.keycloak_admin_client_secret
    kc = (
        (keycloak_realm_url(settings), settings.keycloak_admin_client_id, secret.get_secret_value())
        if secret
        else None
    )
    return Auth(
        TokenVerifier(
            str(settings.keycloak_issuer), settings.keycloak_audience, Jwks(jwks_url(settings))
        ),
        audit=audit,
        directory=SqlUserDirectory(lambda: app_sessions(app.state), audit),
        memberships=SqlMemberships(lambda: app_sessions(app.state)),
        orgs=KeycloakOrgs(*kc) if kc else NoOrgs(),
        users=KeycloakUsers(*kc) if kc else NoUserAdmin(),
    )


def create_app(
    settings: Settings | None = None,
    checks: Mapping[str, Check] | None = None,
    auth: Auth | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="SBAM API",
        docs_url=f"{API_BASE}/docs",
        openapi_url=f"{API_BASE}/openapi.json",
        redoc_url=None,
        # FastAPI's built-in OpenTelemetry would add a second exporter to our provider, own spans,
        # and metrics/log exporters (logs can carry request bodies). core/telemetry.py owns tracing.
        telemetry={"auto_configure": False, "tracing": False, "metrics": False, "logs": False},
    )
    app.state.settings = settings
    app.state.auth = auth or default_auth(app, settings)
    app.state.limiter = RateLimiter(
        settings.rate_limit_writes_per_minute, settings.rate_limit_llm_per_minute
    )
    install_error_handlers(app)
    app.include_router(health_router(checks if checks is not None else default_checks(settings)))
    routers = discover_routers()
    if missing := unguarded_routes(routers):  # every route must say who may call it (E2.2)
        raise RuntimeError(f"routes without an authorization policy: {', '.join(missing)}")
    for router in routers:
        app.include_router(router, prefix=API_BASE)
    # Last added runs first: correlation id wraps the body limit so a 413 also carries the id.
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        # The workbook import (E3.3) and a master document (E3.8) take a 15 MB file plus the
        # multipart framing.
        per_path={
            f"{API_BASE}/admin/masters/import": 16 * 1024 * 1024,
            f"{API_BASE}/admin/master-documents": 16 * 1024 * 1024,
        },
    )
    app.add_middleware(CorrelationIdMiddleware)
    configure_telemetry(app, settings)  # wraps the whole stack: one server span per request
    return app
