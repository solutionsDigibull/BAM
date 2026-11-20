"""Build preflight (#925): refuse a build up front when the platform cannot finish it.

`build_manifest`, the first stage of every Sub-Skill build, pins the active skill-runtime registry's
image digest. A fresh stack has none until `make runtime-registry` loads it, so every build ended in
BUILD_ERROR with ProvenanceMissing, one Sub-Skill after the other. The routes that start or restart
a build call this first: nothing is signalled, no Sub-Skill moves, the FDE gets the reason and the
fix in plain words. 409 CONFLICT (the existing code; a dedicated one would be a contract change).
"""

import asyncio
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.conventions import ApiError
from sbam_api.registry.repository import active_registry

RUNTIME_MISSING_TITLE = "Skill runtime registry is not loaded"
RUNTIME_MISSING_DETAIL = (
    "Builds pin the skill-runtime image, and no runtime registry is loaded on this stack. "
    "Ask the platform team to run `make runtime-registry` (or `make runtime-registry-load` for a "
    "built image), then start the build again. Nothing was started."
)


def runtime_registry_loaded(session: Session) -> bool:
    registry = active_registry(session)
    return registry is not None and registry.image_digest is not None


def require_build_prerequisites(session: Session) -> None:
    """409 CONFLICT naming the missing prerequisite and the fix; None when a build can start."""
    if not runtime_registry_loaded(session):
        raise ApiError(409, ErrorCode.CONFLICT, RUNTIME_MISSING_TITLE, RUNTIME_MISSING_DETAIL)


def prerequisite_checks(
    sessions: Callable[[], sessionmaker[Session]],
) -> dict[str, Callable[[], Awaitable[bool]]]:
    """Readiness lines for `/health/ready` (#925): shown, never failing the probe. A fresh stack
    is healthy but cannot build until the registry is loaded: the line says `missing`, not `fail`.
    """

    async def runtime_registry() -> bool:
        def probe() -> bool:
            with sessions()() as session:
                return runtime_registry_loaded(session)

        return await asyncio.to_thread(probe)

    return {"runtime_registry": runtime_registry}
