"""Masters engine (TD §7.3, E3.2). The router only maps HTTP onto these.

* The tenant comes from the token (`user.context()`); another tenant's entry, project or snapshot is
  a 404 like a missing one.
* Admins create ORGANIZATION / RULES / AGENTS / SKILLS / TOOLING entries. FILETYPE (and README) come
  only from the super-admin workbook import (E3.3): 403 here.
* The payload is validated by the type's model (no extra keys; errors at `payload.<field>`).
  `stable_id`, `status` and `source_kind` come from `masters.identify` and live on the entry.
* A version is never changed: a new payload is version n+1 (`If-Match` = the entry's ETag). A stale
  ETag, or losing the race on (entry, version), answers 409 VERSION_CONFLICT. The stable id can't
  change in a new version; a different id is a different entry.
* Every create and version is audited with the entry and version ids.
* Context outdated (T07): `context_outdated()` compares a stored snapshot with what the project
  would pin now. The Knowledge module calls it to mark its versions OUTDATED; nothing starts a run.
"""

import uuid
from typing import Any

from pydantic import ValidationError
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.masters import ADMIN_EDITABLE, MasterIdentity, MasterType, identify
from sbam_contracts.masters_api import MasterEntryCreate, MasterVersionCreate, SnapshotEntry
from sbam_contracts.masters_workbook import PAYLOAD_MODELS, ROW_LABEL_PREFIX, TOOLING_LABEL
from sbam_contracts.projects import ProjectStatus
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, etag, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import StaleRowVersion
from sbam_api.masters.models import MasterEntry, MasterEntryVersion, MasterSnapshot
from sbam_api.masters.repository import MasterEntryRepository
from sbam_api.masters.snapshot import Candidate, resolve, snapshot_hash
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository


def version_conflict() -> ApiError:
    return ApiError(
        409, ErrorCode.VERSION_CONFLICT, "Another version was saved first", "Reload the entry."
    )


def _invalid(errors: list[FieldError]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid master entry", errors=errors)


def _not_editable(master_type: MasterType) -> ApiError:
    return ApiError(
        403,
        ErrorCode.FORBIDDEN,
        "This master is read-only here",
        f"{master_type.value} changes only through the super-admin workbook import.",
    )


def _repo(session: Session, user: CurrentUser) -> MasterEntryRepository:
    return MasterEntryRepository(session, user.context())


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


# --- validation -----------------------------------------------------------------------------


def _editable(master_type: MasterType) -> None:
    if master_type not in ADMIN_EDITABLE:
        raise _not_editable(master_type)


def _identity(
    master_type: MasterType, row_label: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], MasterIdentity]:
    """Validate the payload with the type's model; derive the SBAM metadata from the row."""
    try:
        model = PAYLOAD_MODELS[master_type].model_validate(payload)
    except ValidationError as exc:
        raise _invalid(
            [
                FieldError(
                    path=".".join(["payload", *(str(p) for p in e["loc"])]), message=e["msg"]
                )
                for e in exc.errors()
            ]
        ) from None
    try:
        identity = identify(master_type, row_label, model)
    except ValueError as exc:  # e.g. an unknown or doubled `Status:` fragment in Misc
        raise _invalid([FieldError(path="payload", message=str(exc))]) from None
    return model.model_dump(mode="json", by_alias=True), identity


def _row_label(
    repo: MasterEntryRepository,
    master_type: MasterType,
    project_id: uuid.UUID | None,
    label: str | None,
) -> str:
    if master_type == MasterType.TOOLING:
        if not label or not TOOLING_LABEL.fullmatch(label):
            raise _invalid(
                [FieldError(path="row_label", message="Tooling rows need an id like TOOL-MFG-12")]
            )
        return label
    prefix = ROW_LABEL_PREFIX[master_type]
    if label is None:
        return f"{prefix} #{repo.next_label_number(master_type.value, project_id)}"
    if not label.startswith(f"{prefix} #"):
        raise _invalid([FieldError(path="row_label", message=f"use '{prefix} #n'")])
    return label


def _active_project(session: Session, user: CurrentUser, project_id: uuid.UUID) -> Project:
    """A project of the caller's tenant (else 404) that isn't archived (else 409)."""
    project: Project | None = ProjectRepository(session, user.context()).get(project_id)
    if project is None:
        raise not_found()
    if project.status == ProjectStatus.ARCHIVED:
        raise ApiError(409, ErrorCode.PROJECT_ARCHIVED, "This project is archived and read-only")
    return project


# --- entries and versions -------------------------------------------------------------------


def create_entry(
    session: Session, user: CurrentUser, master_type: MasterType, body: MasterEntryCreate
) -> tuple[MasterEntry, MasterEntryVersion]:
    _editable(master_type)
    repo = _repo(session, user)
    if body.project_id is not None:
        _active_project(session, user, body.project_id)
    label = _row_label(repo, master_type, body.project_id, body.row_label)
    payload, identity = _identity(master_type, label, body.payload)
    entry = MasterEntry(
        project_id=body.project_id,
        master_type=master_type.value,
        stable_id=identity.stable_id,
        row_label=label,
        status=identity.status.value,
        source_kind=identity.source_kind.value,
        current_version=1,
        created_by=user.keycloak_sub,
    )
    try:
        repo.add(entry)
        version = repo.add_version(
            MasterEntryVersion(
                entry_id=entry.id, version=1, payload=payload, created_by=user.keycloak_sub
            )
        )
        _audit(session, user, "MASTER_ENTRY_CREATED", entry, version)
        session.commit()
    except IntegrityError:  # the stable id already exists in this scope
        session.rollback()
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "This master already has an entry with this id",
            f"{identity.stable_id} exists; add a version to it instead.",
        ) from None
    return entry, version


def new_version(
    session: Session,
    user: CurrentUser,
    master_type: MasterType,
    entry_id: uuid.UUID,
    body: MasterVersionCreate,
    if_match: str | None,
) -> tuple[MasterEntry, MasterEntryVersion]:
    _editable(master_type)
    repo = _repo(session, user)
    entry = repo.get_for_update(entry_id)
    if entry is None or entry.master_type != master_type.value:
        raise not_found()
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    if if_match.strip().removeprefix("W/") != etag(entry.row_version):
        raise version_conflict()  # someone saved a version since this one was loaded
    payload, identity = _identity(master_type, entry.row_label, body.payload)
    if identity.stable_id != entry.stable_id:
        raise _invalid(
            [
                FieldError(
                    path="payload",
                    message=f"the stable id stays {entry.stable_id}; a new id is a new entry",
                )
            ]
        )
    number = entry.current_version + 1
    try:
        version = repo.add_version(
            MasterEntryVersion(
                entry_id=entry.id, version=number, payload=payload, created_by=user.keycloak_sub
            )
        )
        entry.current_version = number
        entry.status = identity.status.value
        repo.update(entry, entry.row_version)  # a concurrent writer since load -> stale
        _audit(session, user, "MASTER_VERSION_CREATED", entry, version)
        session.commit()
    except (IntegrityError, StaleRowVersion):  # lost the race on (entry, version n+1)
        session.rollback()
        raise version_conflict() from None
    return entry, version


def list_entries(
    session: Session,
    user: CurrentUser,
    master_type: MasterType,
    project_id: uuid.UUID | None,
) -> list[tuple[MasterEntry, MasterEntryVersion]]:
    repo = _repo(session, user)
    if project_id is not None:
        _active_project(session, user, project_id)
    entries = repo.of_type(master_type.value, project_id)
    versions = repo.current_versions(entries)
    return [(e, versions[e.id]) for e in entries if e.id in versions]


def get_entry(
    session: Session, user: CurrentUser, master_type: MasterType, entry_id: uuid.UUID
) -> tuple[MasterEntry, MasterEntryVersion]:
    repo = _repo(session, user)
    entry = repo.get(entry_id)
    if entry is None or entry.master_type != master_type.value:
        raise not_found()
    version = repo.version(entry)
    if version is None:
        raise not_found()
    return entry, version


# --- snapshots ------------------------------------------------------------------------------


def _resolve(repo: MasterEntryRepository, project_id: uuid.UUID) -> list[SnapshotEntry]:
    entries = repo.for_project(project_id)
    versions = repo.current_versions(entries)
    return resolve(
        project_id,
        (
            Candidate(
                entry_id=e.id,
                project_id=e.project_id,
                master_type=e.master_type,
                stable_id=e.stable_id,
                status=e.status,
                version=e.current_version,
                payload=versions[e.id].payload,
            )
            for e in entries
            if e.id in versions
        ),
    )


def create_snapshot(session: Session, user: CurrentUser, project_id: uuid.UUID) -> MasterSnapshot:
    """Pin the project's current master versions (callers: SOW generation, knowledge builds)."""
    _active_project(session, user, project_id)
    repo = _repo(session, user)
    pinned = _resolve(repo, project_id)
    snapshot = repo.add_snapshot(
        MasterSnapshot(
            project_id=project_id,
            entries=[e.model_dump(mode="json") for e in pinned],
            sha256=snapshot_hash(project_id, pinned),
            created_by=user.keycloak_sub,
        )
    )
    session.commit()
    return snapshot


def get_snapshot(session: Session, user: CurrentUser, snapshot_id: uuid.UUID) -> MasterSnapshot:
    snapshot = _repo(session, user).snapshot(snapshot_id)
    if snapshot is None:
        raise not_found()
    return snapshot


def context_outdated(session: Session, user: CurrentUser, snapshot_id: uuid.UUID) -> bool:
    """True when the masters changed since `snapshot_id` was pinned (T07). Starts nothing."""
    repo = _repo(session, user)
    snapshot = repo.snapshot(snapshot_id)
    if snapshot is None:
        raise not_found()
    now = _resolve(repo, snapshot.project_id)
    return snapshot_hash(snapshot.project_id, now) != snapshot.sha256


# --- audit ----------------------------------------------------------------------------------


def _audit(
    session: Session,
    user: CurrentUser,
    action: str,
    entry: MasterEntry,
    version: MasterEntryVersion,
) -> None:
    record(
        session,
        tenant_id=entry.tenant_id,
        actor_id=str(user.user_id),
        action=action,
        entity_type="master_entry",
        entity_id=str(entry.id),
        after={
            "master_type": entry.master_type,
            "stable_id": entry.stable_id,
            "status": entry.status,
            "project_id": str(entry.project_id) if entry.project_id else None,
            "version": version.version,
            "version_id": str(version.id),
        },
        correlation_id=_correlation(),
    )
