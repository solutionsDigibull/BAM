"""The two seams Commit calls that other issues own (#766).

`StagingRuntime` is the Staging runtime of #765 (start the container on the certified digest, serve
the UI bundle). It is not built yet, so the default adapter only says so: `PENDING_RUNTIME` is
recorded and shown, and Commit can be run again once the real runtime exists. #765 replaces
`default_runtime` (or registers its own adapter); tests use a fake.

`WorkflowFreezer` is the Workflow-level freeze of #132 (E14.4: ordered Sub-Skills, Macros, Workflow
and MAJOR.0.PATCH), still a stub in the pipeline. `StateMachineFreezer` is the smallest honest
version: it merges the UI bundle entry of `workflow_ui_built` (#768) into the manifest, seals
that as a new frozen manifest row with a WORKFLOW certificate derived from the one the build
earned (the evidence names the hash it came from and the bundle digest), and applies the `freeze`
event. It certifies nothing new: a Workflow that was not certified is refused before this runs.
#132 swaps it for the full freeze behind the same protocol.
"""

import uuid
from typing import Protocol

from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import Actor, WorkflowState
from sbam_contracts.workflow_commit import (
    CommitCheckName,
    StagingStartRequest,
    StagingStartResult,
    StagingStartStatus,
)
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.certifications.models import CertificationLevel
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.conventions import ApiError
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import WorkflowManifestRepository
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import StorageBackend
from sbam_api.workflow_commit import checks
from sbam_api.workflows.models import Workflow


class StagingRuntime(Protocol):
    def start(self, request: StagingStartRequest) -> StagingStartResult:
        """Start (or find already running) the container of `request.image_digest`. Idempotent:
        the same version and digest again answers the running one."""
        ...


class PendingStagingRuntime:
    """Until #765 exists: nothing starts, and the answer says why."""

    def start(self, request: StagingStartRequest) -> StagingStartResult:
        return StagingStartResult(
            status=StagingStartStatus.PENDING_RUNTIME,
            detail="the Staging runtime (#765) is not available yet; the request is recorded",
        )


def default_runtime() -> StagingRuntime:
    return PendingStagingRuntime()


class WorkflowFreezer(Protocol):
    def freeze(
        self,
        session: Session,
        ctx: TenantContext,
        workflow_id: uuid.UUID,
        manifest_hash: str,
        correlation_id: str,
        storage: StorageBackend,
    ) -> None:
        """Freeze the Workflow on exactly the manifest approved as `manifest_hash`. Idempotent:
        already frozen is a no-op. Refuses (409) a manifest that changed or a bundle that no longer
        matches its recorded digest."""
        ...


class FreezeRefused(ApiError):  # noqa: N818
    def __init__(self, reason: str) -> None:
        super().__init__(409, ErrorCode.CONFLICT, "Cannot freeze", reason)


class StateMachineFreezer:
    """The interim freezer of #766. #132 adds `versioning.freezer.OrderedFreezer`, which extends it
    (it reuses `eligible`, `seal` and `transition`) and is the default of the worker."""

    def freeze(
        self,
        session: Session,
        ctx: TenantContext,
        workflow_id: uuid.UUID,
        manifest_hash: str,
        correlation_id: str,
        storage: StorageBackend,
    ) -> None:
        found = self.eligible(session, ctx, workflow_id, manifest_hash, storage)
        self.seal(session, ctx, found, correlation_id)
        self.transition(session, ctx, found, manifest_hash, correlation_id)
        session.flush()

    @staticmethod
    def eligible(
        session: Session,
        ctx: TenantContext,
        workflow_id: uuid.UUID,
        manifest_hash: str,
        storage: StorageBackend,
    ) -> checks.Evaluation:
        found = checks.evaluate(session, ctx, workflow_id, storage)
        if not found.approved(manifest_hash) or found.row is None:
            raise FreezeRefused("the manifest changed since it was approved")
        gated = (CommitCheckName.UI_BUNDLE, CommitCheckName.MCP_SERVER)
        for check in found.checks:
            if check.name in gated and not check.passed:
                raise FreezeRefused(f"{check.name.value}: {check.reason}")
        if found.workflow.state not in checks.CERTIFIED_STATES:
            raise FreezeRefused(f"the Workflow is {found.workflow.state}, not CERTIFIED")
        return found

    @staticmethod
    def seal(
        session: Session, ctx: TenantContext, found: checks.Evaluation, correlation_id: str
    ) -> None:
        workflow_id, newest = found.workflow.id, found.row
        if newest is None:
            raise FreezeRefused("there is no Workflow manifest yet")
        sealed = found.effective
        if sealed is not None and sealed.integrity.content_sha256 != newest.sha256:
            # The first freeze of this manifest: the version with the bundle entry becomes the
            # frozen row and gets the certificate the build earned, derived from it.
            built = sealed.integrity.content_sha256
            repo = WorkflowManifestRepository(session, ctx)
            repo.record(
                {
                    "tenant_id": ctx.tenant_id,
                    "project_id": ctx.project_id,
                    "workflow_id": workflow_id,
                    "build_no": newest.build_no + 1,
                    "manifest": sealed.dump(),
                    "sha256": built,
                    "is_frozen": True,
                }
            )
            CertificationRepository(session, ctx).record(
                {
                    "tenant_id": ctx.tenant_id,
                    "project_id": ctx.project_id,
                    "level": CertificationLevel.WORKFLOW.value,
                    "subject_id": workflow_id,
                    "content_sha256": built,
                    "evidence": {
                        "derived_from": newest.sha256,
                        "ui_bundle_digest": sealed.ui_bundle.digest if sealed.ui_bundle else None,
                    },
                    "certified_at": utcnow(),
                }
            )
            record(
                session,
                tenant_id=ctx.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action=checks.FROZEN_ACTION,
                entity_type="workflow",
                entity_id=str(workflow_id),
                correlation_id=correlation_id,
                after={"from_hash": newest.sha256, "to_hash": built},
                idempotency_key=f"{checks.FROZEN_ACTION}:{newest.sha256}",
            )
        elif not newest.is_frozen:
            newest.is_frozen = True

    @staticmethod
    def transition(
        session: Session,
        ctx: TenantContext,
        found: checks.Evaluation,
        manifest_hash: str,
        correlation_id: str,
    ) -> None:
        if found.workflow.state == WorkflowState.CERTIFIED.value:
            apply_transition(
                session,
                ctx,
                TransitionCommand(
                    model=Workflow,
                    entity_id=found.workflow.id,
                    event="freeze",
                    actor=Actor.SYSTEM,
                    actor_id=SYSTEM_ACTOR_ID,
                    correlation_id=correlation_id,
                    idempotency_key=f"commit-freeze:{manifest_hash}",
                ),
            )
