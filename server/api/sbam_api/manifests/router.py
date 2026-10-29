"""Manifest read endpoints (E9.6, TD §8.8), for the Manifest viewer. Read-only, FDE portal.

* `GET W/sub-skills/{sub_id}/manifests`: the Sub-Skill's manifests, newest first, one per hash.
* `GET W/sub-skills/{sub_id}/manifest?version=&build_no=`: one of them (default: the newest);
  `version` is MAJOR.0.PATCH (else 422). 404 before the first build.
* `GET W/manifests` and `GET W/manifest?build_no=`: the Workflow manifest of each build (E9.5).
* `GET /schemas/manifest/{name}`: the JSON Schema of `skill_manifest` / `workflow_manifest`, for the
  editor's hover; any signed-in user, conditional requests answer 304.

Another tenant's or an unassigned project, a Workflow or Sub-Skill of another project: 404.
"""

import hashlib
import json
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel
from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ProblemDetail
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import SkillManifest, WorkflowManifest
from sbam_contracts.manifest_api import VERSION_PATTERN, ManifestVersionList, ManifestView
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, authenticated, fde, project_access
from sbam_api.core.conventions import not_found
from sbam_api.isatvon.router import CACHE_CONTROL, _matches
from sbam_api.manifests import service
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["manifests"])

W = "/projects/{project_id}/workflows/{workflow_id}"
Project = Annotated[UUID, Depends(project_access)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
BuildNo = Annotated[int | None, Query(ge=1)]
SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "skill_manifest": SkillManifest,
    "workflow_manifest": WorkflowManifest,
}


@router.get(W + "/sub-skills/{sub_skill_id}/manifests")
def sub_skill_manifests(
    project_id: Project, workflow_id: UUID, sub_skill_id: UUID, user: User, sessions: Sessions
) -> ManifestVersionList:
    with sessions() as session:
        return service.sub_skill_versions(
            session, user.context(project_id), workflow_id, sub_skill_id
        )


@router.get(W + "/sub-skills/{sub_skill_id}/manifest")
def sub_skill_manifest(
    project_id: Project,
    workflow_id: UUID,
    sub_skill_id: UUID,
    user: User,
    sessions: Sessions,
    version: Annotated[str | None, Query(pattern=VERSION_PATTERN)] = None,
    build_no: BuildNo = None,
) -> ManifestView:
    with sessions() as session:
        return service.sub_skill_manifest(
            session, user.context(project_id), workflow_id, sub_skill_id, version, build_no
        )


@router.get(W + "/manifests")
def workflow_manifests(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> ManifestVersionList:
    with sessions() as session:
        return service.workflow_versions(session, user.context(project_id), workflow_id)


@router.get(W + "/manifest")
def workflow_manifest(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions, build_no: BuildNo = None
) -> ManifestView:
    with sessions() as session:
        return service.workflow_manifest(session, user.context(project_id), workflow_id, build_no)


@router.get(
    "/schemas/manifest/{name}",
    dependencies=[Depends(authenticated)],
    responses={
        200: {"content": {"application/json": {}}, "description": "The JSON Schema"},
        304: {"description": "If-None-Match matched the ETag; no body"},
        404: {"model": ProblemDetail, "description": "Not a manifest schema"},
    },
)
def manifest_schema(name: str, if_none_match: Annotated[str | None, Header()] = None) -> Response:
    model = SCHEMAS.get(name)
    if model is None:
        raise not_found()
    document = contract_schema(model, f"manifest/{name}")
    etag = '"' + hashlib.sha256(canonical_json(document)).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    if _matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    return Response(content=body, media_type="application/json", headers=headers)
