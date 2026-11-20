"""Liveness and readiness.

Bodies carry only check names and ok/fail, plus each bulkhead's ok/saturated: no hosts, versions or
errors.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from temporalio.client import Client

from sbam_api.core.bulkhead import REGISTRY
from sbam_api.core.settings import Settings, jwks_url
from sbam_api.db.session import API_TIMEOUTS, make_engine, ping

Check = Callable[[], Awaitable[bool]]
CHECK_TIMEOUT_SECONDS = 2.0
log = logging.getLogger("sbam_api.health")


def default_checks(settings: Settings) -> dict[str, Check]:
    jwks = jwks_url(settings)

    engine = make_engine(
        settings.database_url, timeouts=API_TIMEOUTS
    )  # lazy: connects on the first check

    async def database() -> bool:
        return await asyncio.to_thread(ping, engine)

    async def storage() -> bool:
        # Write + delete a probe file: os.access(W_OK) misses Windows ACLs and some RO mounts.
        probe = settings.storage_root / f".ready-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True

    clients: list[Client] = []  # connected once, reused by later checks

    async def temporal() -> bool:
        if not clients:
            host, namespace = settings.temporal_host, settings.temporal_namespace
            clients.append(await Client.connect(host, namespace=namespace))
        return await clients[0].service_client.check_health()

    async def keycloak_jwks() -> bool:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(jwks)
        return response.status_code == 200 and "keys" in response.json()

    return {
        "db": database,
        "storage": storage,
        "temporal": temporal,
        "keycloak_jwks": keycloak_jwks,
    }


async def _run(name: str, check: Check) -> bool:
    try:
        return await asyncio.wait_for(check(), CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # any failure, including timeout, means "fail"
        log.warning("readiness check failed", extra={"check": name, "exc_type": type(exc).__name__})
        return False


async def _prerequisites(info: Mapping[str, Check]) -> dict[str, str]:
    names = list(info)
    outcomes = await asyncio.gather(
        *(asyncio.wait_for(info[n](), CHECK_TIMEOUT_SECONDS) for n in names),
        return_exceptions=True,
    )
    return {
        n: ("unknown" if isinstance(o, BaseException) else "ok" if o else "missing")
        for n, o in zip(names, outcomes, strict=True)
    }


def health_router(
    checks: Mapping[str, Check], info: Mapping[str, Check] | None = None
) -> APIRouter:
    """`info` lines are shown under `prerequisites` as ok / missing / unknown and never change the
    status: a stack that waits for a one-off setup step is healthy (#925)."""

    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready")
    async def ready() -> JSONResponse:
        names = list(checks)
        results = await asyncio.gather(*(_run(n, checks[n]) for n in names))
        report: dict[str, Any] = {
            n: "ok" if ok else "fail" for n, ok in zip(names, results, strict=True)
        }
        healthy = all(results)
        # Informational: a saturated dependency fails fast on its own; it does not make the API
        # unready (that would pull a healthy replica out for a slow scanner).
        bulkheads = {b.name: "saturated" if b.saturated else "ok" for b in REGISTRY}
        body: dict[str, Any] = {
            "status": "ok" if healthy else "fail",
            "checks": report,
            "bulkheads": bulkheads,
        }
        if info:
            body["prerequisites"] = await _prerequisites(info)
        return JSONResponse(body, status_code=200 if healthy else 503)

    return router
