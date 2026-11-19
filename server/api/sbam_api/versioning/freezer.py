"""The ordered freeze of a Workflow (#132, E14.4, TD §13.4, ADR 0036): the real `WorkflowFreezer`.

Order, all idempotent so a crash anywhere is finished by running it again:

1. eligibility: every certification check of Commit passes on the stored evidence (the WORKFLOW
   certificate, each Sub-Skill's certificate on its newest bundle, the built server, the UI bundle
   evidence). The freeze never certifies anything;
2. files: every stored file the version consists of (code bundles, schema.sql) is read back and
   hashed. A mismatch is `HashMismatch`: nothing was changed, the Workflow stays CERTIFIED;
3. the DB writes, one transaction: Sub-Skills freeze, then their Macro Skills, the file index rows
   and newest Sub-Skill manifests get `is_frozen` (the DB trigger now refuses any change), the
   Workflow manifest is sealed with its WORKFLOW certificate, the `workflow_versions` row is
   written and the Workflow's `freeze` event is applied. Every state change goes through the
   state-machine service, so it is audited;
4. read-back: the manifest hashes are recomputed and the files read again; a mismatch raises, the
   caller rolls the transaction back and the version is not frozen;
5. the Sub-Skills of earlier Workflows (an older approved SOW) become SUPERSEDED. Frozen rows are
   never edited: "superseded" of a Workflow version is derived from the newer version.
"""

import logging
import uuid
from dataclasses import dataclass

from sbam_contracts.api import ErrorCode
from sbam_contracts.manifest import SkillManifest, WorkflowManifest
from sbam_contracts.state_machines import Actor, AgentState, SubSkillState, WorkflowState
from sbam_contracts.workflow_commit import CommitCheckName
from sqlalchemy.orm import Session

from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.audit.repository import entity_history, record
from sbam_api.builds.models import CodeAttempt
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.core.conventions import ApiError
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.manifests.repository import SubSkillManifestRepository, WorkflowManifestRepository
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import StorageBackend
from sbam_api.versioning import numbers
from sbam_api.versioning.repository import WorkflowVersionRepository
from sbam_api.workflow_commit import checks
from sbam_api.workflow_commit.ports import FreezeRefused, StateMachineFreezer
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

log = logging.getLogger(__name__)

PATCH_STARTED = "WORKFLOW_PATCH_STARTED"  # audit: {sub_skill_id, next_version, change_reason}
VERSION_FROZEN = "WORKFLOW_VERSION_FROZEN"  # audit: {version}

# The Macro Skill machine is not advanced by the pipeline yet (#130, E14.2). Once eligibility has
# shown every Sub-Skill certified, the freeze brings its Macro Skill up to CERTIFIED and freezes it.
_MACRO_STEPS = {
    AgentState.PENDING.value: ("build_started", "all_sub_skills_certified", "freeze"),
    AgentState.BUILDING.value: ("all_sub_skills_certified", "freeze"),
    AgentState.CERTIFIED.value: ("freeze",),
}


class HashMismatch(ApiError):  # noqa: N818 - the E14.4 / E26 name
    def __init__(self, what: str) -> None:
        super().__init__(
            409,
            ErrorCode.HASH_MISMATCH,
            "Hash mismatch",
            f"{what}; the version is not frozen and the Workflow stays CERTIFIED",
        )


@dataclass(frozen=True)
class FrozenFile:
    key: str  # content-addressed: the key ends in the file's sha256


def _system(
    session: Session,
    ctx: TenantContext,
    model: type,
    entity_id: uuid.UUID,
    event: str,
    correlation_id: str,
    key: str,
) -> None:
    apply_transition(
        session,
        ctx,
        TransitionCommand(
            model=model,
            entity_id=entity_id,
            event=event,
            actor=Actor.SYSTEM,
            actor_id=SYSTEM_ACTOR_ID,
            correlation_id=correlation_id,
            idempotency_key=key,
        ),
    )


class OrderedFreezer(StateMachineFreezer):
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
        workflow = found.workflow
        subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
        attempts = newest_attempts(session, ctx, workflow_id, subs)
        files = [FrozenFile(a.bundle_key) for a in attempts if a.bundle_key]
        sql = None if found.effective is None else found.effective.schema_sql
        if sql is not None:
            files.append(FrozenFile(sql.artifact_key))
        read_back(storage, files)  # before anything changes: a damaged file stops the freeze
        version = self.next_version(session, ctx, workflow)
        self.freeze_children(session, ctx, workflow_id, subs, version, correlation_id)
        self.freeze_files(session, ctx, attempts)
        self.freeze_sub_manifests(session, ctx, subs)
        self.seal(session, ctx, found, correlation_id)
        sealed = WorkflowManifestRepository(session, ctx).history(workflow_id)[0]
        self.record_version(session, ctx, workflow, version, sealed, correlation_id)
        self.transition(session, ctx, found, manifest_hash, correlation_id)
        workflow.version = str(version)
        session.flush()
        try:
            verify_manifests(session, ctx, workflow_id, subs)
            read_back(storage, files)
        except HashMismatch as error:
            log.error("freeze read-back failed for workflow %s: %s", workflow_id, error.detail)
            raise
        self.supersede_earlier(session, ctx, workflow, correlation_id)
        session.flush()

    # 1. eligibility

    @staticmethod
    def eligible(
        session: Session,
        ctx: TenantContext,
        workflow_id: uuid.UUID,
        manifest_hash: str,
        storage: StorageBackend,
    ) -> checks.Evaluation:
        found = StateMachineFreezer.eligible(session, ctx, workflow_id, manifest_hash, storage)
        for check in found.checks:
            if check.name is not CommitCheckName.SECRETS and not check.passed:
                raise FreezeRefused(f"{check.name.value}: {check.reason}")
        if found.effective is None or found.row is None:
            raise FreezeRefused("there is no Workflow manifest yet")
        return found

    # 3. the DB writes

    @staticmethod
    def freeze_children(
        session: Session,
        ctx: TenantContext,
        workflow_id: uuid.UUID,
        subs: list[SubSkill],
        version: numbers.Version,
        correlation_id: str,
    ) -> None:
        """Sub-Skills first, then their Macro Skills. Frozen ones stay as they are (a patch)."""
        for sub in subs:
            if sub.state == SubSkillState.CERTIFIED.value:
                key = f"freeze-{version}"
                _system(session, ctx, SubSkill, sub.id, "freeze", correlation_id, key)
        for macro in MacroSkillRepository(session, ctx).of_workflow(workflow_id):
            for event in _MACRO_STEPS.get(macro.state, ()):
                key = f"freeze-{version}-{event}"
                _system(session, ctx, MacroSkill, macro.id, event, correlation_id, key)

    @staticmethod
    def freeze_files(session: Session, ctx: TenantContext, attempts: list[CodeAttempt]) -> None:
        index = ArtifactRepository(session, ctx)
        for attempt in attempts:
            artifact = index.find(attempt.bundle_key) if attempt.bundle_key else None
            if artifact is not None and not artifact.is_frozen:
                artifact.is_frozen = True

    @staticmethod
    def freeze_sub_manifests(session: Session, ctx: TenantContext, subs: list[SubSkill]) -> None:
        newest = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
        for manifest in newest.values():
            if not manifest.is_frozen:
                manifest.is_frozen = True

    @staticmethod
    def next_version(session: Session, ctx: TenantContext, workflow: Workflow) -> numbers.Version:
        taken = [
            r.version for r in WorkflowVersionRepository(session, ctx).of_workflow(workflow.id)
        ]
        if workflow.state == WorkflowState.FROZEN.value and taken:
            return max(map(numbers.parse, taken))  # a re-run of a freeze that already happened
        return numbers.decide(workflow.major, taken)

    @staticmethod
    def record_version(
        session: Session,
        ctx: TenantContext,
        workflow: Workflow,
        version: numbers.Version,
        manifest: WorkflowManifestRow,
        correlation_id: str,
    ) -> None:
        repo = WorkflowVersionRepository(session, ctx)
        if any(r.version == str(version) for r in repo.of_workflow(workflow.id)):
            return
        reason = "First freeze"
        if version.patch > 0:
            reason = "Patch under the same SOW"
            for logged in entity_history(session, ctx.tenant_id, "workflow", str(workflow.id)):
                after = logged.after or {}
                if logged.action == PATCH_STARTED and after.get("next_version") == str(version):
                    reason = str(after.get("change_reason", reason))
        elif workflow.major > 1:
            reason = "New approved SOW version"
        repo.record(
            {
                "tenant_id": ctx.tenant_id,
                "project_id": ctx.project_id,
                "workflow_id": workflow.id,
                "version": str(version),
                "major": version.major,
                "patch": version.patch,
                "sow_version_id": workflow.sow_version_id,
                "workflow_manifest_id": manifest.id,
                "change_reason": reason[:500],
                "frozen_at": utcnow(),
            }
        )
        record(
            session,
            tenant_id=ctx.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action=VERSION_FROZEN,
            entity_type="workflow",
            entity_id=str(workflow.id),
            correlation_id=correlation_id,
            after={"version": str(version)},
            idempotency_key=f"{VERSION_FROZEN}:{workflow.id}:{version}",
        )

    # 5. earlier versions

    @staticmethod
    def supersede_earlier(
        session: Session, ctx: TenantContext, workflow: Workflow, correlation_id: str
    ) -> None:
        """A newer approved SOW (higher MAJOR) supersedes the frozen Sub-Skills of older ones."""
        for other in WorkflowRepository(session, ctx).of_project():
            if other.major >= workflow.major:
                continue
            for sub in SubSkillRepository(session, ctx).of_workflow(other.id):
                if sub.state == SubSkillState.FROZEN.value:
                    key = f"superseded-by-{workflow.id}"
                    _system(session, ctx, SubSkill, sub.id, "superseded", correlation_id, key)


def newest_attempts(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, subs: list[SubSkill]
) -> list[CodeAttempt]:
    """Each Sub-Skill's newest code attempt that has a stored bundle."""
    repo = CodeAttemptRepository(session, ctx)
    found = []
    for sub in subs:
        usable = [a for a in repo.of_sub_skill(workflow_id, sub.id) if a.bundle_key]
        if usable:
            found.append(usable[-1])
    return found


def read_back(storage: StorageBackend, files: list[FrozenFile]) -> None:
    """Re-read and hash every file. Missing or different bytes is `HashMismatch`."""
    for item in files:
        try:
            storage.get(item.key, verify=True)
        except Exception as error:
            raise HashMismatch(f"{item.key} cannot be read back intact") from error


def verify_manifests(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, subs: list[SubSkill]
) -> None:
    """T09: every frozen manifest hashes to the hash it records."""
    for row in SubSkillManifestRepository(session, ctx).newest([s.id for s in subs]).values():
        try:
            manifest = SkillManifest.model_validate(row.manifest)
        except ValueError as error:
            raise HashMismatch(f"the manifest of {row.sub_skill_id} is not valid") from error
        if manifest.computed_hash() != manifest.governance.deterministic_hash:
            raise HashMismatch(f"the manifest of {row.sub_skill_id} does not match its hash")
    newest = WorkflowManifestRepository(session, ctx).history(workflow_id)[0]
    sealed = WorkflowManifest.model_validate(newest.manifest)
    if sealed.computed_hash() != newest.sha256 or sealed.integrity.content_sha256 != newest.sha256:
        raise HashMismatch("the Workflow manifest does not match its hash")
