"""Re-scoring relevance (#802): `POST /projects/{id}/documents/relevance/rescore` starts
`RelevanceRescore` for the project, which scores every parsed document against the current goal.

* The project must have a goal: 409 CONFLICT otherwise (there is nothing to score against).
* One execution per project (`relevance-<project_id>`); a newer start replaces a running one, so the
  latest goal wins. 202 with the run; 503 when Temporal is down (nothing changes).
* Advice only: nothing here or in the workflow changes a document's state.
"""

import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import RELEVANCE_RESCORE, relevance_workflow_id
from sbam_contracts.runs import Run, RunInput, SubjectType, TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.temporal.service import StartSpec, start_run


def no_goal() -> ApiError:
    return ApiError(
        409,
        ErrorCode.CONFLICT,
        "No goal to score against",
        "Set the project's goal first; documents are scored against it.",
    )


def has_goal(sessions: sessionmaker[Session], user: CurrentUser, project_id: uuid.UUID) -> bool:
    with sessions() as session:
        project: Project | None = ProjectRepository(session, user.context()).get(project_id)
        return project is not None and bool(project.goal)


async def start_rescore(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    *,
    with_search_attributes: bool = True,
) -> Run:
    """Start `RelevanceRescore`; see the module docstring. The caller checked `has_goal`."""
    ctx = user.context(project_id)
    client = await get_client()  # OrchestratorUnavailable -> 503
    spec = StartSpec(
        workflow=RELEVANCE_RESCORE,
        workflow_id=relevance_workflow_id(project_id),
        queue=TaskQueue.INGEST,
        subject_type=SubjectType.PROJECT,
        subject_id=project_id,
        stage="relevance",
        input=RunInput(
            tenant_id=ctx.tenant_id,
            project_id=project_id,
            run_id=uuid7(),
            correlation_id=correlation_id_var.get() or str(uuid7()),
        ),
    )
    return await start_run(
        client,
        sessions,
        spec,
        with_search_attributes=with_search_attributes,
        conflict=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
    )
