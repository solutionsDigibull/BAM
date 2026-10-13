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
from sqlalchemy.orm import Session, sessionmaker

import sbam_api
from sbam_api.audit.security import AuditLogSecurityAudit
from sbam_api.core.auth import POLICIES, Auth, Jwks, TokenVerifier
from sbam_api.core.errors import install_error_handlers
from sbam_api.core.health import Check, default_checks, health_router
from sbam_api.core.logging import configure_logging
from sbam_api.core.middleware import BodyLimitMiddleware, CorrelationIdMiddleware
from sbam_api.core.settings import Settings, jwks_url, load_settings
from sbam_api.core.telemetry import configure_telemetry
from sbam_api.db.session import make_engine, make_sessionmaker

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

    def sessions() -> sessionmaker[Session]:  # one pool per app, built on first use
        if getattr(app.state, "sessions", None) is None:
            app.state.sessions = make_sessionmaker(make_engine(settings.database_url))
        result: sessionmaker[Session] = app.state.sessions
        return result

    app.state.auth = auth or Auth(
        TokenVerifier(
            str(settings.keycloak_issuer), settings.keycloak_audience, Jwks(jwks_url(settings))
        ),
        audit=AuditLogSecurityAudit(sessions),  # denials go to audit_log (#41, ADR 0017)
    )
    install_error_handlers(app)
    app.include_router(health_router(checks if checks is not None else default_checks(settings)))
    routers = discover_routers()
    if missing := unguarded_routes(routers):  # every route must say who may call it (E2.2)
        raise RuntimeError(f"routes without an authorization policy: {', '.join(missing)}")
    for router in routers:
        app.include_router(router, prefix=API_BASE)
    # Last added runs first: correlation id wraps the body limit so a 413 also carries the id.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(CorrelationIdMiddleware)
    configure_telemetry(app, settings)  # wraps the whole stack: one server span per request
    return app
