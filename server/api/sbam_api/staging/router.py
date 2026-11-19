"""Staging routes (#765). W is `/projects/{project_id}/workflows/{workflow_id}`.

* `GET W/staging`: the deployment's state, health and history (what #861's toggle reads).
* `GET W/staging/ui/{path}`: a file of the certified UI bundle, checked against its digest.
* `POST W/staging/mcp`, `DELETE W/staging/mcp`: the workflow's MCP server (streamable HTTP), reached
  through SBAM so the container is never exposed.

Only the assigned FDE (`active_project`): another tenant, an unassigned FDE, a missing Workflow
and one that was never committed to Staging are 404. A Staging container that is not running is 409.
"""

import asyncio
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sbam_contracts.api import ErrorCode
from sbam_contracts.staging import StagingHealth, StagingStatus
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import ApiError
from sbam_api.projects.guard import active_project
from sbam_api.staging import service
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["staging"])

Project = Annotated[UUID, Depends(active_project)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
Storage = Annotated[StorageBackend, Depends(backend)]
W = service.W
PROBE_TIMEOUT = 2.0
FORWARD_TIMEOUT = 60.0
MAX_BODY = 4 * 1024 * 1024
PASS_REQUEST = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version")
PASS_RESPONSE = ("content-type", "mcp-session-id", "mcp-protocol-version")


def _client(request: Request, timeout: float) -> httpx.AsyncClient:
    """`app.state.staging_transport` lets a test stand in for the container."""
    transport = getattr(request.app.state, "staging_transport", None)
    return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)


async def _health(request: Request, endpoint: str) -> StagingHealth:
    """Any answer below 500 means the workflow server is up: its MCP endpoint answers a plain GET
    with a 4xx, which is still a live server."""
    try:
        async with _client(request, PROBE_TIMEOUT) as client:
            answer = await client.get(endpoint.rstrip("/") + "/mcp")
    except httpx.HTTPError:
        return StagingHealth.UNREACHABLE
    return StagingHealth.HEALTHY if answer.status_code < 500 else StagingHealth.UNREACHABLE


@router.get(W + "/staging")
async def staging_status(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions, request: Request
) -> StagingStatus:
    ctx = user.context(project_id)

    def target() -> str | None:
        with sessions() as session:
            return service.health_target(session, ctx, workflow_id)

    endpoint = await asyncio.to_thread(target)
    health = None if endpoint is None else await _health(request, endpoint)

    def read() -> StagingStatus:
        with sessions() as session:
            return service.status(session, ctx, workflow_id, health)

    return await asyncio.to_thread(read)


@router.get(W + "/staging/ui/{path:path}")
def staging_ui(
    project_id: Project,
    workflow_id: UUID,
    path: str,
    user: User,
    sessions: Sessions,
    storage: Storage,
) -> Response:
    with sessions() as session:
        served = service.ui_file(session, user.context(project_id), workflow_id, storage, path)
    return Response(
        served.data,
        media_type=served.content_type,
        headers={
            "Content-Security-Policy": service.content_security_policy(served.allowed_hosts),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


async def _forward(
    request: Request,
    method: str,
    project_id: UUID,
    workflow_id: UUID,
    user: CurrentUser,
    sessions: sessionmaker[Session],
) -> Response:
    ctx = user.context(project_id)

    def target() -> str:
        with sessions() as session:
            return service.endpoint_of_running(session, ctx, workflow_id)

    endpoint = await asyncio.to_thread(target)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ApiError(413, ErrorCode.VALIDATION_ERROR, "The request body is too large")
    headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_REQUEST}
    try:
        async with _client(request, FORWARD_TIMEOUT) as client:
            answer = await client.request(
                method, endpoint.rstrip("/") + "/mcp", content=body or None, headers=headers
            )
    except httpx.HTTPError:
        raise service.not_running("the workflow server did not answer") from None
    passed = {k: v for k, v in answer.headers.items() if k.lower() in PASS_RESPONSE}
    return Response(answer.content, status_code=answer.status_code, headers=passed)


@router.post(W + "/staging/mcp")
async def staging_mcp_post(
    project_id: Project, workflow_id: UUID, request: Request, user: User, sessions: Sessions
) -> Response:
    return await _forward(request, "POST", project_id, workflow_id, user, sessions)


@router.delete(W + "/staging/mcp")
async def staging_mcp_delete(
    project_id: Project, workflow_id: UUID, request: Request, user: User, sessions: Sessions
) -> Response:
    return await _forward(request, "DELETE", project_id, workflow_id, user, sessions)
