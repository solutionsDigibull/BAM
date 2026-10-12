"""Liveness and readiness.

Bodies carry only check names and ok/fail: no hosts, versions or errors.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from sbam_api.core.settings import Settings, jwks_url
from sbam_api.db.session import make_engine, ping

Check = Callable[[], Awaitable[bool]]
CHECK_TIMEOUT_SECONDS = 2.0
log = logging.getLogger("sbam_api.health")


async def _tcp(host: str, port: int) -> bool:
    _, writer = await asyncio.open_connection(host, port)
    writer.close()
    await writer.wait_closed()
    return True


def default_checks(settings: Settings) -> dict[str, Check]:
    temporal_host, temporal_port = settings.temporal_host.rsplit(":", 1)
    jwks = jwks_url(settings)

    engine = make_engine(settings.database_url)  # lazy: connects on the first check

    async def database() -> bool:
        return await asyncio.to_thread(ping, engine)

    async def storage() -> bool:
        # Write + delete a probe file: os.access(W_OK) misses Windows ACLs and some RO mounts.
        probe = settings.storage_root / f".ready-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True

    async def temporal() -> bool:
        # ponytail: TCP reachability only; #40 (E1.7) swaps this for the client health check.
        return await _tcp(temporal_host, int(temporal_port))

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


def health_router(checks: Mapping[str, Check]) -> APIRouter:
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
        return JSONResponse(
            {"status": "ok" if healthy else "fail", "checks": report},
            status_code=200 if healthy else 503,
        )

    return router
