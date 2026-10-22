"""Admin projects (TD §8.3, E3.1). The router only maps HTTP onto these.

* The tenant comes from the caller's token (`user.context()`), never from the body; another tenant's
  project is a 404 like a missing one.
* A project is created through the state-machine service (`create_project` -> IDLE), which writes
  the state's audit row; PROJECT_CREATED / UPDATED / ARCHIVED rows carry the field diff.
* Names are unique per tenant ignoring case. The pre-check gives the friendly answer; the unique
  index decides a race (IntegrityError -> 409 DUPLICATE_NAME).
* Archive locks the row (SELECT … FOR UPDATE), so a pipeline transition waits for it or wins first.
* Members (E2.4): PUT replaces the set under the same row lock. Every id must be an ACTIVE sbam-fde
  of the tenant; each bad one is a 422 error on `user_ids.<i>` (another tenant's user looks
  missing). PROJECT_MEMBERS_CHANGED carries the ids before and after.
"""

import uuid
from typing import Any

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.identity import UserStatus
from sbam_contracts.projects import ProjectCreate, ProjectMembersPut, ProjectPatch, ProjectStatus
from sbam_contracts.state_machines import Actor
from sbam_contracts.state_machines import ProjectPipelineState as P
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import ROLE_FDE, CurrentUser
from sbam_api.core.conventions import ApiError, check_if_match, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.identity.models import User
from sbam_api.identity.repository import UserRepository
from sbam_api.projects.models import Project, ProjectMember
from sbam_api.projects.repository import MemberRepository, ProjectRepository
from sbam_api.state_machine import apply_create

FIELDS = ("name", "client_name", "description", "context_notes", "allow_restricted")

# Nothing is running in these states.
# TODO(#91): also refuse while a Workflow is BUILDING or CERTIFYING.
ARCHIVABLE = frozenset(
    {
        P.IDLE,
        P.INGESTION_FAILED,
        P.KNOWLEDGE_FAILED,
        P.SOW_GENERATION_FAILED,
        P.SOW_REVIEW,
        P.WORKFLOW_CREATED,
        P.CANCELLED,
    }
)


def duplicate_name() -> ApiError:
    return ApiError(409, ErrorCode.DUPLICATE_NAME, "A project with this name already exists")


def project_archived() -> ApiError:
    return ApiError(409, ErrorCode.PROJECT_ARCHIVED, "This project is archived and read-only")


def pipeline_running() -> ApiError:
    return ApiError(409, ErrorCode.PIPELINE_RUNNING, "The project pipeline is running")


def require_active(project: Project) -> Project:
    """For writes on a project (FDE routes too): an archived project answers 409."""
    if project.status == ProjectStatus.ARCHIVED:
        raise project_archived()
    return project


def _repo(session: Session, user: CurrentUser) -> ProjectRepository:
    return ProjectRepository(session, user.context())


def get(session: Session, user: CurrentUser, project_id: uuid.UUID) -> Project:
    project: Project | None = _repo(session, user).get(project_id)
    if project is None:
        raise not_found()
    return project


def list_all(session: Session, user: CurrentUser) -> list[Project]:
    # ponytail: one page of 100, oldest first; cursor paging when a tenant has more projects.
    return list(_repo(session, user).list())


def list_assigned(session: Session, user: CurrentUser) -> list[Project]:
    """FDE `/projects`: assigned and ACTIVE only; an archived project disappears."""
    return _repo(session, user).active_among(user.assigned_project_ids)


def create(session: Session, user: CurrentUser, body: ProjectCreate) -> Project:
    ctx = user.context()
    if ProjectRepository(session, ctx).name_taken(body.name):
        raise duplicate_name()
    project = Project(**body.model_dump(), created_by=user.keycloak_sub)
    try:
        apply_create(
            session,
            ctx,
            project,
            event="create_project",
            actor=Actor.ADMIN,
            actor_id=str(user.user_id),
            correlation_id=_correlation(),
        )
        _audit(session, user, "PROJECT_CREATED", project, None, _fields(project, FIELDS))
        session.commit()
    except IntegrityError:  # lost the race on the unique name
        session.rollback()
        raise duplicate_name() from None
    return project


def update(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    body: ProjectPatch,
    if_match: str | None,
) -> Project:
    repo = _repo(session, user)
    project: Project | None = repo.get(project_id)
    if project is None:
        raise not_found()
    check_if_match(if_match, project.row_version)
    require_active(project)
    changes = {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if getattr(project, k) != v
    }
    if not changes:
        return project
    if "name" in changes and repo.name_taken(changes["name"], exclude=project.id):
        raise duplicate_name()
    before = _fields(project, changes)
    for column, value in changes.items():
        setattr(project, column, value)
    try:
        repo.update(project, project.row_version)  # concurrent writer since load -> 412
        # ponytail: a context_notes change is in this diff; the knowledge module marks its
        # context outdated from it once knowledge versions exist.
        _audit(session, user, "PROJECT_UPDATED", project, before, _fields(project, changes))
        session.commit()
    except IntegrityError:
        session.rollback()
        raise duplicate_name() from None
    return project


def archive(session: Session, user: CurrentUser, project_id: uuid.UUID) -> Project:
    repo = _repo(session, user)
    project: Project | None = repo.get_for_update(project_id)
    if project is None:
        raise not_found()
    if project.status == ProjectStatus.ARCHIVED:
        return project  # archiving twice is a no-op
    if project.state not in ARCHIVABLE:
        raise pipeline_running()
    project.status = ProjectStatus.ARCHIVED.value
    repo.update(project, project.row_version)
    _audit(
        session,
        user,
        "PROJECT_ARCHIVED",
        project,
        {"status": ProjectStatus.ACTIVE.value, "pipeline_state": project.state},
        {"status": project.status},
    )
    session.commit()
    return project


def members(session: Session, user: CurrentUser, project_id: uuid.UUID) -> list[User]:
    get(session, user, project_id)  # 404 for a missing or other-tenant project
    ids = MemberRepository(session, user.context()).user_ids(project_id)
    return _sorted(UserRepository(session, user.context()).among(ids).values())


def set_members(
    session: Session, user: CurrentUser, project_id: uuid.UUID, body: ProjectMembersPut
) -> list[User]:
    ctx = user.context()
    project: Project | None = _repo(session, user).get_for_update(project_id)
    if project is None:
        raise not_found()
    require_active(project)
    found = UserRepository(session, ctx).among(body.user_ids)
    errors = [
        FieldError(path=f"user_ids.{i}", message="not an active FDE of this tenant")
        for i, uid in enumerate(body.user_ids)
        if not _assignable(found.get(uid))
    ]
    if errors:
        raise ApiError(
            422, ErrorCode.VALIDATION_ERROR, "Only active FDEs can be assigned", errors=errors
        )
    repo = MemberRepository(session, ctx)
    before, after = repo.user_ids(project.id), set(body.user_ids)
    if before != after:
        repo.remove(project.id, before - after)
        for uid in sorted(after - before):
            member = ProjectMember(project_id=project.id, user_id=uid, created_by=user.keycloak_sub)
            repo.add(member)
        _audit(
            session,
            user,
            "PROJECT_MEMBERS_CHANGED",
            project,
            {"user_ids": sorted(map(str, before))},
            {"user_ids": sorted(map(str, after))},
        )
        session.commit()
    return _sorted(found.values())


def _assignable(row: User | None) -> bool:
    return row is not None and row.status == UserStatus.ACTIVE and ROLE_FDE in row.portal_roles


def _sorted(users: Any) -> list[User]:
    return sorted(users, key=lambda u: u.id)


def _fields(project: Project, names: Any) -> dict[str, Any]:
    return {name: getattr(project, name) for name in names}


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _audit(
    session: Session,
    user: CurrentUser,
    action: str,
    project: Project,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    record(
        session,
        tenant_id=project.tenant_id,
        actor_id=str(user.user_id),
        action=action,
        entity_type="project",
        entity_id=str(project.id),
        before=before,
        after=after,
        correlation_id=_correlation(),
    )
