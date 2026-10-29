"""The archived-project guard for every project-scoped route (A-26, #452).

`active_project` is the `project_access` dependency (FDE policy; other tenant, unassigned or
malformed id: 404) plus one more rule: a write on an archived project is 409 `PROJECT_ARCHIVED`.
Reads (GET, HEAD, OPTIONS) pass, so an archived project can still be looked at. The 404 comes first,
so a caller who may not see the project learns nothing about whether it is archived.

A router uses it as its `Project` path-parameter type; a route that is a POST but writes nothing
(a validation report) keeps plain `project_access` and is listed in
`tests/api/test_project_write_guard.py`, which fails for any other project-scoped write without it.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import not_found
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import require_active

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def active_project(
    project_id: Annotated[uuid.UUID, Depends(project_access)],
    request: Request,
    user: Annotated[CurrentUser, Depends(fde)],
) -> uuid.UUID:
    """The assigned project's id; for a write, only while the project is not archived."""
    if request.method in READ_METHODS:
        return project_id
    # Imported here: `sbam_api.temporal` imports its router, which imports this module.
    from sbam_api.temporal.deps import session_factory

    sessions: sessionmaker[Session] = session_factory(request)
    with sessions() as session:
        project = ProjectRepository(session, user.context()).get(project_id)
        if project is None:
            raise not_found()
        require_active(project)
    return project_id
