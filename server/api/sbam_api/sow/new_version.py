"""POST P/sow/new-version (E7.2, C-15): the FDE starts a scope change from the approved SOW.

Under the `sows` row lock, in one transaction:

* an APPROVED version must exist (409 NO_APPROVED_SOW) and no draft may be open: the current
  version is the approved one, else 409 DRAFT_EXISTS (edit or approve that draft instead);
* the draft is a copy of the approved version (`EDITED`, IN_REVIEW, `parent_version_id` = the
  approved one, the same content and sha256), made by the FDE through the state machine
  (`new_version`), and becomes the current version;
* the project goes back to SOW review (`start_scope_change`, WORKFLOW_CREATED → SOW_REVIEW);
* audit_log gets SOW_NEW_VERSION with the reason and the FDE.
The draft is then edited like any version (`POST P/sow/versions`); its impact on the built
Sub-Skills is `GET P/sow/versions/{v}/impact`.
"""

import uuid

from sbam_contracts.api import ErrorCode
from sbam_contracts.sow_api import SowNewVersionRequest
from sbam_contracts.state_machines import Actor
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, etag
from sbam_api.core.ids import uuid7
from sbam_api.projects.models import Project
from sbam_api.sow.models import SowSource, SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.sow.versions import Saved, summary
from sbam_api.state_machine import TransitionCommand, apply_create, apply_transition

SOW_NEW_VERSION = "SOW_NEW_VERSION"  # audit action


def no_approved_sow() -> ApiError:
    return ApiError(
        409,
        ErrorCode.NO_APPROVED_SOW,
        "No approved SOW",
        "A new version starts from the approved SOW; approve one first.",
    )


def start_new_version(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    body: SowNewVersionRequest,
    correlation_id: str,
) -> Saved:
    ctx = user.context(project_id)
    sows = SowRepository(session, ctx)
    sow = sows.of_project(for_update=True)
    if sow is None or sow.approved_version_id is None:
        raise no_approved_sow()
    versions = SowVersionRepository(session, ctx)
    approved = versions.get(sow.approved_version_id)
    if approved is None:
        raise no_approved_sow()
    if sow.current_version_id != approved.id:
        current = versions.get(sow.current_version_id) if sow.current_version_id else None
        draft = f"v{current.version}" if current is not None else "A draft"
        raise ApiError(
            409,
            ErrorCode.DRAFT_EXISTS,
            "A draft is already open",
            f"{draft} is the open draft; edit or approve it instead of starting another.",
        )

    draft_row = SowVersion(
        id=uuid7(),
        sow_id=sow.id,
        version=versions.next_number(sow.id),
        source=SowSource.EDITED.value,
        run_id=uuid7(),
        knowledge_version_id=approved.knowledge_version_id,
        parent_version_id=approved.id,
        structured=approved.structured,
        content_tiptap=approved.content_tiptap,
        sha256=approved.sha256,
        created_by=str(user.user_id),
    )
    apply_create(
        session,
        ctx,
        draft_row,
        event="new_version",
        actor=Actor.FDE,
        actor_id=str(user.user_id),
        correlation_id=correlation_id,
    )
    apply_transition(
        session,
        ctx,
        TransitionCommand(
            model=Project,
            entity_id=project_id,
            event="start_scope_change",
            actor=Actor.FDE,
            actor_id=str(user.user_id),
            correlation_id=correlation_id,
        ),
    )
    sow.current_version_id = draft_row.id
    sows.update(sow, sow.row_version)  # moves the ETag the editor saves against
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=SOW_NEW_VERSION,
        entity_type="sow_version",
        entity_id=str(draft_row.id),
        correlation_id=correlation_id,
        before={"version": approved.version, "sha256": approved.sha256},
        after={"version": draft_row.version, "reason": body.reason},
    )
    session.commit()
    numbers = {r.id: r.version for r in versions.of_sow(sow.id)}
    return Saved(summary(draft_row, numbers), etag(sow.row_version), created=True)
