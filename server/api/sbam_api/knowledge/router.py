"""Knowledge endpoints (TD §8, E5.2).

* `POST P/knowledge/rebuild`: 202 `{"run": Run}`; the new knowledge version is BUILDING. 404
  unassigned or other tenant; 409 KNOWLEDGE_BUILDING while a version is being built (or the
  pipeline is about to build one), PROJECT_ARCHIVED; 503 TEMPORAL_UNAVAILABLE. A repeated
  `Idempotency-Key` answers the same run.
* `GET P/knowledge`: the project's versions, newest first (E5.4).
* `GET P/knowledge/{kv_id}/graph?cursor&limit&type&q`: a page of the version's graph, from its OKF
  bundle. 409 KNOWLEDGE_NOT_READY while BUILDING / FAILED; OUTDATED stays readable. 400
  INVALID_CURSOR for a cursor of another version or filter, or edited.
* `GET P/knowledge/{kv_id}/nodes/{node_id}`: the node, its neighbours (<= 100) and its source
  passages (<= 4000 characters each; the full text is in the bundle).
* `GET P/knowledge/{kv_id}/okf-bundle`: 302 to a signed URL (300 s) of the bundle's six parts as a
  zip. Read-only: no route changes a node or an edge. 20 requests/s per user, then 429.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import RedirectResponse
from sbam_contracts.knowledge import (
    MAX_GRAPH_LIMIT,
    KnowledgeGraphPage,
    KnowledgeNodeDetail,
    KnowledgeVersionList,
)
from sbam_contracts.runs import RunAccepted
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.settings import Settings
from sbam_api.knowledge import graph
from sbam_api.knowledge.service import start_rebuild
from sbam_api.pipeline.service import MAX_IDEMPOTENCY_KEY
from sbam_api.storage.router import backend
from sbam_api.storage.signing import signed_url
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["knowledge"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY)]


@router.post(
    "/projects/{project_id}/knowledge/rebuild", status_code=202, response_model=RunAccepted
)
async def rebuild(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    run = await start_rebuild(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        idempotency_key=idempotency_key,
        with_search_attributes=settings.temporal_search_attributes,
    )
    return RunAccepted(run=run)


OKF_URL_TTL = 300
NodeId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")]


def limited(user: User) -> CurrentUser:
    graph.limiter.check(str(user.user_id))
    return user


Reader = Annotated[CurrentUser, Depends(limited)]


def _secret(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    key = settings.storage_signing_key
    return key.get_secret_value() if key else None


def _cursor_secret(request: Request) -> bytes:
    return (_secret(request) or "sbam-knowledge-cursor").encode()


@router.get("/projects/{project_id}/knowledge")
def versions(project_id: Project, user: Reader, sessions: Sessions) -> KnowledgeVersionList:
    with sessions() as session:
        return graph.list_versions(session, user.context(project_id))


@router.get("/projects/{project_id}/knowledge/{kv_id}/graph")
def graph_page(
    project_id: Project,
    kv_id: UUID,
    request: Request,
    user: Reader,
    sessions: Sessions,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_GRAPH_LIMIT)] = 100,
    type: Annotated[str | None, Query(max_length=1000)] = None,  # the API's name for it
    q: Annotated[str | None, Query(max_length=1000)] = None,
) -> KnowledgeGraphPage:
    types, query = graph.clean_types(type), graph.clean_query(q)
    with sessions() as session:
        row = graph.readable_version(session, user.context(project_id), kv_id)
    loaded = graph.load(backend(request), row)
    return graph.graph_page(
        loaded,
        row,
        secret=_cursor_secret(request),
        cursor=cursor,
        limit=limit,
        types=types,
        q=query,
    )


@router.get("/projects/{project_id}/knowledge/{kv_id}/nodes/{node_id}")
def node(
    project_id: Project,
    kv_id: UUID,
    node_id: NodeId,
    request: Request,
    user: Reader,
    sessions: Sessions,
) -> KnowledgeNodeDetail:
    ctx = user.context(project_id)
    with sessions() as session:
        row = graph.readable_version(session, ctx, kv_id)
        loaded = graph.load(backend(request), row)
        return graph.node_detail(session, ctx, loaded, row, node_id)


@router.get(
    "/projects/{project_id}/knowledge/{kv_id}/okf-bundle",
    status_code=302,
    response_class=RedirectResponse,
)
def okf_bundle(
    project_id: Project, kv_id: UUID, request: Request, user: Reader, sessions: Sessions
) -> RedirectResponse:
    ctx = user.context(project_id)
    with sessions() as session:
        row = graph.readable_version(session, ctx, kv_id)
    storage = backend(request)
    key = graph.store_zip(storage, ctx, graph.bundle_zip(storage, graph.load(storage, row)))
    return RedirectResponse(signed_url(_secret(request), key, ctx, OKF_URL_TTL), status_code=302)
