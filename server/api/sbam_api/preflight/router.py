"""`GET /preflight` (any signed-in FDE or admin): is the platform ready for real work.

Reuses the readiness checks for the platform (database, storage, workflow engine, sign-in) and adds
the two provider probes. The answer is cached for a minute for the whole process: the probes bill a
fraction of a cent each, and a page that polls must not turn that into real money.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sbam_contracts.preflight import CheckStatus, Preflight, PreflightCheck

from sbam_api.core.auth import CurrentUser, admin_or_fde
from sbam_api.core.settings import Settings
from sbam_api.preflight.checks import platform_check, provider_probes

router = APIRouter(tags=["preflight"])
CACHE_SECONDS = 60.0
_ORDER = {CheckStatus.OK: 0, CheckStatus.MOCK: 1, CheckStatus.DEGRADED: 2, CheckStatus.DOWN: 3}

User = Annotated[CurrentUser, Depends(admin_or_fde)]


def worst(checks: list[PreflightCheck]) -> CheckStatus:
    return max((c.status for c in checks), key=_ORDER.__getitem__, default=CheckStatus.OK)


async def run_all(
    platform: dict[str, Callable[[], Awaitable[bool]]],
    providers: dict[str, Callable[[], Coroutine[Any, Any, PreflightCheck]]],
) -> Preflight:
    results = await asyncio.gather(
        *(platform_check(name, check) for name, check in platform.items()),
        *(probe() for probe in providers.values()),
    )
    checks = list(results)
    return Preflight(
        status=worst(checks),
        checks=checks,
        checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


@router.get("/preflight")
async def preflight(request: Request, user: User) -> Preflight:
    state = request.app.state
    cached = getattr(state, "preflight_cache", None)
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        report: Preflight = cached[1]
        return report
    settings: Settings = state.settings
    report = await run_all(dict(state.health_checks), provider_probes(settings))
    state.preflight_cache = (time.monotonic(), report)
    return report
