"""SBAM API app factory. Run: `uvicorn sbam_api.main:create_app --factory`.

Feature modules are never registered here: each `sbam_api.<module>` that exposes `router` is
discovered and mounted under /api/v1.
"""

import importlib
import pkgutil
from collections.abc import Mapping
from types import ModuleType

from fastapi import APIRouter, FastAPI
from sbam_contracts.api import API_BASE

import sbam_api
from sbam_api.core.errors import install_error_handlers
from sbam_api.core.health import Check, default_checks, health_router
from sbam_api.core.logging import configure_logging
from sbam_api.core.middleware import BodyLimitMiddleware, CorrelationIdMiddleware
from sbam_api.core.settings import Settings, load_settings

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


def create_app(
    settings: Settings | None = None, checks: Mapping[str, Check] | None = None
) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="SBAM API",
        docs_url=f"{API_BASE}/docs",
        openapi_url=f"{API_BASE}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health_router(checks if checks is not None else default_checks(settings)))
    for router in discover_routers():
        app.include_router(router, prefix=API_BASE)
    # Last added runs first: correlation id wraps the body limit so a 413 also carries the id.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(CorrelationIdMiddleware)
    return app
