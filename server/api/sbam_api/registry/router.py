"""Runtime registry API (TD §8.3, §8.15, E12.1): the active skill-runtime image and its packages.

GET /api/v1/admin/registry -> admin portal (sbam-admin / super-admin)
GET /api/v1/registry       -> FDE portal, read-only
Same handler; 404 until a registry has been synced.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sbam_contracts.runtime_registry import RuntimeRegistry
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import admin, fde
from sbam_api.core.conventions import not_found
from sbam_api.registry.repository import active_registry
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["registry"])
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


def _active(sessions: sessionmaker[Session]) -> RuntimeRegistry:
    with sessions() as session:
        registry = active_registry(session)
    if registry is None:
        raise not_found()
    return registry


@router.get("/admin/registry", response_model=RuntimeRegistry, dependencies=[Depends(admin)])
def admin_registry(sessions: Sessions) -> RuntimeRegistry:
    return _active(sessions)


@router.get("/registry", response_model=RuntimeRegistry, dependencies=[Depends(fde)])
def fde_registry(sessions: Sessions) -> RuntimeRegistry:
    return _active(sessions)
