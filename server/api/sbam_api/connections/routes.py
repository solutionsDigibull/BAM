"""Project connections: the FDE's keys and settings, write-only (ADR 0030, decision 12; #764).

* `GET  /projects/{p}/connections`: the merged list, expected names and stored entries.
* `GET  /projects/{p}/connections/{name}`: one entry.
* `PUT  /projects/{p}/connections/{name}/value`: set or replace (`{"value": ...}`; `If-Match`).
* `DELETE /projects/{p}/connections/{name}/value`: clear (`If-Match`).
* `PUT  /projects/{p}/connections/{name}`: purpose, where to get it, required flag, test mode and
  target (`If-Match`).
* `POST /projects/{p}/connections/{name}/test`: test the stored key against the FDE's URL (10 a
  minute per user, behind the SSRF guard).
* `GET  /projects/{p}/workflows/{w}/connections/readiness`: what blocks Commit (#766).
* `GET  /projects/{p}/workflows/{w}/connections/env-example`: `.env.example`, names only.

**No route returns a value, ever**: every response is a model with a status and a masked tail
(a contract test walks their schemas). Assigned FDE only: Admins have no access, and another
tenant's project, an unassigned project or an unknown name is a 404, never a 403.
"""

import math
import threading
import time
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from sbam_contracts.api import ErrorCode
from sbam_contracts.connections import (
    NAME_PATTERN,
    Connection,
    ConnectionList,
    ConnectionTestConfig,
    ConnectionTestResult,
    ConnectionUpdate,
    ConnectionValueIn,
    ConnectionValueResult,
    Readiness,
)
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.connections import service
from sbam_api.connections.crypto import KeyRing, SecretsKeyError
from sbam_api.connections.keys import keyring_from_settings
from sbam_api.connections.ssrf import ProbeResult, probe
from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import ApiError, etag
from sbam_api.core.ratelimit import SlidingWindow
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["connections"])

TESTS_PER_MINUTE = 10

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; another tenant: 404
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
Name = Annotated[str, Path(pattern=NAME_PATTERN)]
IfMatch = Annotated[str | None, Header()]

_lock = threading.Lock()


def _keyring(request: Request) -> KeyRing:
    try:
        return keyring_from_settings(request.app.state.settings)
    except SecretsKeyError:
        raise ApiError(
            503,
            ErrorCode.SERVICE_UNAVAILABLE,
            "Secrets are not available",
            "The server has no key to encrypt connection values with.",
        ) from None


def _probe(request: Request) -> Callable[[ConnectionTestConfig, str], ProbeResult]:
    """The SSRF-guarded call. Tests replace it on `app.state.connection_probe`."""
    found = getattr(request.app.state, "connection_probe", None)
    return found if found is not None else probe


def _test_budget(request: Request, user: CurrentUser) -> None:
    """10 tests a minute per user, on top of the general write budget."""
    with _lock:
        window = getattr(request.app.state, "connection_test_window", None)
        if window is None:
            window = request.app.state.connection_test_window = SlidingWindow(TESTS_PER_MINUTE)
        wait = window.hit(str(user.user_id), time.monotonic())
    if wait:
        seconds = str(math.ceil(wait))
        raise ApiError(
            429,
            ErrorCode.RATE_LIMITED,
            "Too many requests",
            f"Too many connection tests. Try again in {seconds} s.",
            headers={"Retry-After": seconds},
        )


def _etag(response: Response, entry: Connection) -> None:
    if entry.row_version:
        response.headers["ETag"] = etag(entry.row_version)


@router.get("/projects/{project_id}/connections")
def list_connections(project_id: Project, user: User, sessions: Sessions) -> ConnectionList:
    with sessions() as session:
        return service.list_connections(session, user.context(project_id))


@router.get("/projects/{project_id}/connections/{name}")
def get_connection(
    project_id: Project, name: Name, user: User, sessions: Sessions, response: Response
) -> Connection:
    with sessions() as session:
        entry = service.get_connection(session, user.context(project_id), name)
    _etag(response, entry)
    return entry


@router.put("/projects/{project_id}/connections/{name}/value")
def set_connection_value(
    project_id: Project,
    name: Name,
    body: ConnectionValueIn,
    request: Request,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> ConnectionValueResult:
    """Write-only: the answer has the status, the masked tail and which clean-ups ran."""
    keyring = _keyring(request)
    with sessions() as session:
        result = service.set_value(
            session,
            user.context(project_id),
            str(user.user_id),
            name,
            body.value,
            keyring=keyring,
            if_match=if_match,
        )
    _etag(response, result.connection)
    return result


@router.delete("/projects/{project_id}/connections/{name}/value")
def clear_connection_value(
    project_id: Project,
    name: Name,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> Connection:
    with sessions() as session:
        entry = service.clear_value(
            session, user.context(project_id), str(user.user_id), name, if_match=if_match
        )
    _etag(response, entry)
    return entry


@router.put("/projects/{project_id}/connections/{name}")
def update_connection(
    project_id: Project,
    name: Name,
    body: ConnectionUpdate,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> Connection:
    with sessions() as session:
        entry = service.update_entry(
            session, user.context(project_id), str(user.user_id), name, body, if_match=if_match
        )
    _etag(response, entry)
    return entry


@router.post("/projects/{project_id}/connections/{name}/test")
def test_connection_route(
    project_id: Project,
    name: Name,
    request: Request,
    user: User,
    sessions: Sessions,
) -> ConnectionTestResult:
    """A real call to the service the FDE named, behind the SSRF guard. Reports what happened."""
    keyring = _keyring(request)
    _test_budget(request, user)
    with sessions() as session:
        return service.test_connection(
            session,
            user.context(project_id),
            str(user.user_id),
            name,
            keyring=keyring,
            probe=_probe(request),
        )


@router.get("/projects/{project_id}/workflows/{workflow_id}/connections/readiness")
def connections_readiness(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> Readiness:
    """What blocks Commit: a required name with no value, an untested value, a failed test."""
    with sessions() as session:
        return service.commit_blockers(session, user.context(project_id), workflow_id)


@router.get(
    "/projects/{project_id}/workflows/{workflow_id}/connections/env-example",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
)
def connections_env_example(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> Response:
    """`.env.example`: names, purposes and where to get them. Never a value."""
    with sessions() as session:
        text = service.env_example_for(session, user.context(project_id), workflow_id)
    return Response(
        text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )
