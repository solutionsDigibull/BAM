"""`create_workflow` (E8.1, TD §13.2): SBAM creates the Workflow from the approved SOW version.

The ProjectPipeline calls it once the `sow_approved` signal arrived. `create_for_approval` is the
whole step and takes the sessions as an argument, so tests run it on their own database:

  1. the SOW version is re-read in the project: it must still be APPROVED, be the SOW's approved
     version, and its stored and recomputed canonical sha256 must both equal the signal's. Anything
     else is non-retryable `HashMismatch`, logged as an alert, and no Workflow is created;
  2. a Workflow already made for this SOW version is returned as it is (activity retry, duplicate
     signal): one Workflow per approved SOW version;
  3. otherwise the next MAJOR in the project (`<MAJOR>.0.0`, named "<project name> v<MAJOR>") is
     inserted CREATED by SYSTEM through the state-machine service (audit row + notification),
     under the project's row lock; a concurrent insert that wins the unique key is read back.

The pipeline then applies its own `sow_approved` create, which finds the row and does nothing.
"""

import logging
import uuid
from typing import Final

from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.sow.service import canonical_sha256
from sbam_api.state_machine import apply_create
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.pipeline import CreateWorkflowInput, CreateWorkflowResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor, SowVersionState
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.workflows.create")

HASH_MISMATCH: Final = "HashMismatch"
EVENT: Final = "sow_approved"


def _ctx(inp: CreateWorkflowInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _mismatch(inp: CreateWorkflowInput, reason: str) -> ApplicationError:
    log.error(
        "approved SOW does not match the signal; no Workflow created",
        extra={
            "alert": "SOW_HASH_MISMATCH",
            "reason": reason,
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "sow_version_id": str(inp.sow_version_id),
            "correlation_id": inp.correlation_id,
        },
    )
    return ApplicationError(
        f"SOW version is not the approved one with this sha256 ({reason})",
        type=HASH_MISMATCH,
        non_retryable=True,
    )


def verify_approval(session: Session, inp: CreateWorkflowInput) -> None:
    """The SOW version is APPROVED, still the approved one, and hashes to the signal's sha256."""
    ctx = _ctx(inp)
    version: SowVersion | None = SowVersionRepository(session, ctx).get(inp.sow_version_id)
    if version is None or version.state != SowVersionState.APPROVED.value:
        raise _mismatch(inp, "NOT_APPROVED")
    sow = SowRepository(session, ctx).of_project()
    if sow is None or sow.approved_version_id != version.id:
        raise _mismatch(inp, "NOT_CURRENT_APPROVAL")
    if version.structured is None or version.sha256 != inp.sha256:
        raise _mismatch(inp, "STORED_HASH")
    if canonical_sha256(version.structured) != inp.sha256:
        raise _mismatch(inp, "CONTENT_HASH")


def _insert(session: Session, inp: CreateWorkflowInput) -> uuid.UUID:
    ctx = _ctx(inp)
    project: Project | None = ProjectRepository(session, ctx).get_for_update(inp.project_id)
    if project is None:
        raise _mismatch(inp, "NO_PROJECT")
    repo = WorkflowRepository(session, ctx)
    existing = repo.by_sow_version(inp.sow_version_id)  # another attempt won the project lock
    if existing is not None:
        return existing.id
    major = repo.next_major()
    row = Workflow(
        id=uuid7(),
        sow_version_id=inp.sow_version_id,
        major=major,
        version=f"{major}.0.0",
        name=f"{project.name} v{major}",
        created_by="SYSTEM",
    )
    apply_create(
        session,
        ctx,
        row,
        event=EVENT,
        actor=Actor.SYSTEM,
        actor_id=SYSTEM_ACTOR_ID,
        correlation_id=inp.correlation_id,
        idempotency_key=f"{inp.run_id}:create_workflow:{inp.sow_version_id}",
    )
    return row.id


def create_for_approval(
    sessions: sessionmaker[Session], inp: CreateWorkflowInput
) -> CreateWorkflowResult:
    with sessions() as session:
        verify_approval(session, inp)
        existing = WorkflowRepository(session, _ctx(inp)).by_sow_version(inp.sow_version_id)
        if existing is not None:
            return CreateWorkflowResult(workflow_id=existing.id)
        try:
            workflow_id = _insert(session, inp)
            session.commit()
        except IntegrityError:  # a concurrent insert of the same version won the unique key
            session.rollback()
            found = WorkflowRepository(session, _ctx(inp)).by_sow_version(inp.sow_version_id)
            if found is None:
                raise
            workflow_id = found.id
    log.info(
        "workflow created",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "workflow_id": str(workflow_id),
            "correlation_id": inp.correlation_id,
        },
    )
    return CreateWorkflowResult(workflow_id=workflow_id)


@activity_for(TaskQueue.LLM)
@activity.defn(name="create_workflow")
def create_workflow(inp: CreateWorkflowInput) -> CreateWorkflowResult:
    """Verify the approval, then upsert the Workflow on (project_id, sow_version_id)."""
    return create_for_approval(mirror.sessions(), inp)
