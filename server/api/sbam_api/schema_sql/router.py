"""Workflow schema.sql endpoints (E9.4, TD §8.8).

* `GET W/schema-sql?version=`: the newest version (or `version`) of the Workflow's schema.sql with
  its SCHEMA_SQL verdict and findings (`WorkflowSchemaSql`); 404 before any. FDE portal.
* `PUT W/schema-sql/source` (`SchemaSqlSourcePut`): a parsed, clean `.sql` document of the project
  becomes the base the next build uses; 422 SCHEMA_PARSE_ERROR (with the statement) for SQL the
  allowlist refuses, 409 WORKFLOW_BUILDING, 423 WORKFLOW_FROZEN. FDE portal.

Another tenant's or an unassigned project, a Workflow or document of another project: 404.
See `schema_sql/service.py`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sbam_contracts.schema_sql import SchemaSqlSourcePut, WorkflowSchemaSql
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.schema_sql import service
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["schema-sql"])

W = "/projects/{project_id}/workflows/{workflow_id}"
Project = Annotated[UUID, Depends(project_access)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
Storage = Annotated[StorageBackend, Depends(backend)]


@router.get(W + "/schema-sql")
def get_schema_sql(
    project_id: Project,
    workflow_id: UUID,
    user: Annotated[CurrentUser, Depends(fde)],
    sessions: Sessions,
    storage: Storage,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> WorkflowSchemaSql:
    with sessions() as session:
        return service.get_schema(session, storage, user.context(project_id), workflow_id, version)


@router.put(W + "/schema-sql/source")
def set_schema_sql_source(
    project_id: Project,
    workflow_id: UUID,
    body: SchemaSqlSourcePut,
    user: Annotated[CurrentUser, Depends(fde)],
    sessions: Sessions,
    storage: Storage,
) -> WorkflowSchemaSql:
    with sessions() as session:
        out = service.set_source(session, storage, user, project_id, workflow_id, body)
        session.commit()
        return out
