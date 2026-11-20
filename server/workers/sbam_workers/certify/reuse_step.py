"""`reuse_certifications` (E14.3): an unchanged Sub-Skill carries its certificate into the new
Workflow version instead of running the gates again.

Where it runs: right after `carry_over_workflow`, behind its own `workflow.patched` marker, in the
stage that creates the Workflow. It is not a pipeline stage (ADR 0044). Carry-over (E7.2) copied the
untouched, built Sub-Skills as CERTIFIED with `reused_from_id`, but wrote no certificate and
checked nothing. This activity makes that reuse real, one Sub-Skill at a time:

  1. finds the source Sub-Skill and its newest certificate (tenant and project scoped: another
     tenant's or project's row is never found);
  2. re-verifies the source's evidence against storage: the bundle's files hash to the certified
     `bundle_sha256`, the manifest is the frozen one the certificate names, the sandbox run was on
     the certifying host (a local-docker run never certifies, so never reuses) and the test run is
     the one recorded. Any mismatch refuses the reuse;
  3. builds the copy's manifest as the build would now (current template, runtime digest, ISATVON,
     contracts), the copy's current suite hash, and asks `reuse.decide`;
  4. writes an `audit_log` row with the compared hashes (ids and hashes only), either way;
  5. reused: writes the copy's frozen APPROVED manifest (`certification.reused_from` set, version
     kept), the code attempt that points at the same bundle, and the certificate with
     `reused_from_id`. Refused: the copy goes back to VALID through the state machine (audited) and
     is built like any other Sub-Skill; a tampered source also leaves a
     FROZEN_HASH_VERIFICATION_FAILED audit row.

A retry finds the certificates already there and the refused copies no longer CERTIFIED: nothing
is written twice. Nothing here calls a model or reads code into a log.
"""

import asyncio
import copy
import hashlib
import io
import logging
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError
from sbam_api.audit.repository import find_by_key, record
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.models import SubSkillManifest
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.sandbox.repository import SandboxRunRepository
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import StorageBackend
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testruns.repository import TestRunRepository
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import IdsResult, WorkflowRef
from sbam_contracts.promptfoo import suite_sha256
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sandbox import SandboxMode
from sbam_contracts.state_machines import Actor, SubSkillState
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.codegen.bundle import bundle_sha256
from sbam_workers.certify import reuse
from sbam_workers.certify.subskill import approved_manifest
from sbam_workers.registry import activity_for
from sbam_workers.test.suite import SuiteBuildError, build_suite

log = logging.getLogger("sbam_workers.certify.reuse_step")

STAGE: Final = "reuse_certifications"
CREATED_BY: Final = "worker-certify"
REUSED_ACTION: Final = "CERTIFICATION_REUSED"
REFUSED_ACTION: Final = "CERTIFICATION_REUSE_REFUSED"
TAMPER_ACTION: Final = "FROZEN_HASH_VERIFICATION_FAILED"  # the alert of E14.3-T05
AUDIT_ENTITY: Final = "sub_skill"
# Why a source's evidence did not verify (besides `reuse.EVIDENCE_TAMPERED`, the umbrella reason).
NO_SOURCE: Final = "NO_SOURCE_SUB_SKILL"
NOT_CERTIFYING: Final = "SOURCE_NOT_CERTIFYING"
MANIFEST_INVALID: Final = "CURRENT_MANIFEST_INVALID"
BUNDLE_KEY_MISSING: Final = "BUNDLE_NOT_STORED"
# The copy's manifest, as the build would write it now (None: it does not validate).
CurrentManifest = Callable[[WorkflowRef, SubSkill], dict[str, Any] | None]


@dataclass(frozen=True)
class Verified:
    """The source's evidence as it is now: what reuse may carry, or why it may not."""

    problem: str | None
    attempt: CodeAttempt | None = None


def _digest(value: str) -> str:
    return value.removeprefix("sha256:")


def _bundle_files(data: bytes) -> dict[str, str] | None:
    """{file name: sha256} of the stored tar, or None when it is not a plain tar of files."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            hashes: dict[str, str] = {}
            for member in tar.getmembers():
                handle = tar.extractfile(member) if member.isfile() else None
                if handle is None:
                    return None
                hashes[member.name] = hashlib.sha256(handle.read()).hexdigest()
            return hashes
    except (tarfile.TarError, OSError, EOFError):
        return None


class ReuseCertifications:
    """`reuse_certifications` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: StorageBackend,
        current_manifest: CurrentManifest,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.current_manifest = current_manifest
        self.clock = clock

    # --- the source's evidence --------------------------------------------------------------------

    def verify(
        self,
        session: Session,
        ctx: TenantContext,
        source: SubSkill,
        cert: CertificationRow,
    ) -> Verified:
        """Re-verify what the certificate says against the rows and the stored bundle."""
        evidence = cert.evidence
        try:
            attempt_id = uuid.UUID(str(evidence["code_attempt_id"]))
            sandbox_id = uuid.UUID(str(evidence["sandbox_run_id"]))
            test_id = uuid.UUID(str(evidence["test_run_id"]))
            certified_bundle = _digest(str(evidence["bundle_sha256"]))
            manifest_sha = str(evidence["manifest_sha256"])
        except (KeyError, ValueError):
            return Verified(reuse.EVIDENCE_TAMPERED)
        attempt = CodeAttemptRepository(session, ctx).get(attempt_id)
        if attempt is not None and attempt.sub_skill_id != source.id:
            attempt = None  # another Sub-Skill's attempt is not this certificate's evidence
        if (
            attempt is None
            or not attempt.bundle_sha256
            or not attempt.bundle_key
            or attempt.files is None
            or attempt.bundle_sha256 != certified_bundle
            or cert.content_sha256 != "sha256:" + certified_bundle
            or not cert.is_frozen
        ):
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        try:
            data = self.storage.get(attempt.bundle_key)
        except Exception as exc:  # missing or unreadable: the bundle cannot be shown to be intact
            log.warning("source bundle unreadable", extra={"exc_type": type(exc).__name__})
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        hashes = _bundle_files(data)
        if (
            hashes is None
            or hashes != dict(attempt.files)
            or bundle_sha256(hashes) != certified_bundle
        ):
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        manifest = (
            None
            if attempt.manifest_id is None
            else SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)
        )
        if manifest is None or not manifest.is_frozen or manifest.sha256 != manifest_sha:
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        sandbox = SandboxRunRepository(session, ctx).get(sandbox_id)
        if sandbox is None or sandbox.code_attempt_id != attempt.id:
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        if not SandboxMode(sandbox.mode).certifying:  # a local-docker run never certifies
            return Verified(NOT_CERTIFYING, attempt)
        run = TestRunRepository(session, ctx).get(test_id)
        if (
            run is None
            or run.code_attempt_id != attempt.id
            or run.sandbox_run_id != sandbox.id
            or run.suite_sha256 != evidence.get("suite_sha256")
        ):
            return Verified(reuse.EVIDENCE_TAMPERED, attempt)
        return Verified(None, attempt)

    # --- the copy's current parts ----------------------------------------------------------------

    @staticmethod
    def _suite(
        session: Session, ctx: TenantContext, sub: SubSkill, source: SubSkill
    ) -> tuple[str, str]:
        """(suite hash, dataset hash) the copy would be tested with: its own newest test data, else
        the source's (carry-over copies none). "" when there is no suite to hash."""
        datasets = DatasetRepository(session, ctx)
        dataset = datasets.latest(sub.id) or datasets.latest(source.id)
        if dataset is None:
            return "", ""
        cases = [
            {
                "name": row.name,
                "kind": row.kind,
                "input": row.input,
                "expected_terminal": row.expected_terminal,
                "expected_output": row.expected_output,
                "assertions": row.assertions,
            }
            for row in CaseRepository(session, ctx).of_dataset(dataset.id)
        ]
        try:
            return suite_sha256(build_suite(dict(sub.isatvon), cases)), dataset.sha256
        except (SuiteBuildError, ValueError):
            return "", dataset.sha256

    # --- one Sub-Skill ---------------------------------------------------------------------------

    def _audit(
        self,
        session: Session,
        inp: WorkflowRef,
        sub: SubSkill,
        action: str,
        after: dict[str, Any],
    ) -> None:
        key = f"{inp.run_id}:{STAGE}:{sub.id}:{action}"
        if find_by_key(session, inp.tenant_id, AUDIT_ENTITY, str(sub.id), key) is not None:
            return
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action=action,
            entity_type=AUDIT_ENTITY,
            entity_id=str(sub.id),
            correlation_id=inp.correlation_id,
            after=after,
            idempotency_key=key,
        )

    def _one(self, session: Session, ctx: TenantContext, inp: WorkflowRef, sub: SubSkill) -> bool:
        """Reuse or refuse this carried Sub-Skill; True when it now has its reused certificate."""
        source = (
            None
            if sub.reused_from_id is None
            else SubSkillRepository(session, ctx).get(sub.reused_from_id)
        )
        cert = (
            None
            if source is None
            else CertificationRepository(session, ctx).latest(
                CertificationLevel.SUB_SKILL.value, source.id
            )
        )
        verified = Verified(None)
        prior: reuse.ReuseParts | None = None
        current_parts: reuse.ReuseParts | None = None
        manifest: dict[str, Any] | None = None
        suite = dataset = ""
        problem: str | None = None
        old: SubSkillManifest | None = None
        if source is None:
            problem = NO_SOURCE
        elif cert is not None:
            verified = self.verify(session, ctx, source, cert)
            manifest = self.current_manifest(inp, sub)
            suite, dataset = self._suite(session, ctx, sub, source)
            problem = MANIFEST_INVALID if manifest is None else None
            old = self._manifest_of(session, ctx, verified.attempt)
            if old is not None:
                prior = reuse.parts_of(old.manifest, str(cert.evidence.get("suite_sha256", "")))
        if manifest is not None:
            current_parts = reuse.parts_of(manifest, suite)
        decision = reuse.ReuseDecision(False, reason=problem or reuse.NO_PRIOR_CERTIFICATE)
        if current_parts is not None:
            decision = reuse.decide(
                prior, current_parts, evidence_verified=verified.problem is None
            )
        after = {
            "source_sub_skill_id": None if source is None else str(source.id),
            "source_certification_id": None if cert is None else str(cert.id),
            "reuse": decision.reuse,
            "reason": problem or verified.problem or decision.reason,
            "differing": list(decision.differing),
            "prior_key": decision.prior_key,
            "current_key": decision.current_key,
            "prior": None if prior is None else dict(prior.hashes),
            "current": None if current_parts is None else dict(current_parts.hashes),
            "evidence_verified": cert is not None and verified.problem is None,
        }
        if not decision.reuse or cert is None or manifest is None or old is None:
            tampered = verified.problem == reuse.EVIDENCE_TAMPERED
            self._refuse(session, ctx, inp, sub, after, tampered=tampered)
            return False
        self._reuse(session, ctx, inp, sub, cert, verified, old, manifest, dataset, after)
        return True

    @staticmethod
    def _manifest_of(
        session: Session, ctx: TenantContext, attempt: CodeAttempt | None
    ) -> SubSkillManifest | None:
        """The source's manifest, the one its certified code attempt was built from."""
        if attempt is None or attempt.manifest_id is None:
            return None
        return SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)

    def _refuse(
        self,
        session: Session,
        ctx: TenantContext,
        inp: WorkflowRef,
        sub: SubSkill,
        after: dict[str, Any],
        *,
        tampered: bool,
    ) -> None:
        """Back to VALID (invalidate, then validate again: the ISATVON is unchanged, only the code
        is built), audited; the build then treats it like any other Sub-Skill."""
        self._audit(session, inp, sub, REFUSED_ACTION, after)
        if tampered:
            self._audit(session, inp, sub, TAMPER_ACTION, after)
            log.warning(
                "source certificate failed re-verification",
                extra={"sub_skill_id": str(sub.id), "source": after["source_certification_id"]},
            )
        for event in ("scope_changed", "re_extracted", "validation_passed"):
            apply_transition(
                session,
                ctx,
                TransitionCommand(
                    model=SubSkill,
                    entity_id=sub.id,
                    event=event,
                    actor=Actor.SYSTEM,
                    actor_id=SYSTEM_ACTOR_ID,
                    correlation_id=inp.correlation_id,
                    idempotency_key=f"{inp.run_id}:{STAGE}:{sub.id}:{event}",
                ),
            )
        refreshed = SubSkillRepository(session, ctx).get(sub.id)
        if refreshed is not None:
            refreshed.reused_from_id = None  # a rebuilt Sub-Skill is not a reused one

    def _reuse(
        self,
        session: Session,
        ctx: TenantContext,
        inp: WorkflowRef,
        sub: SubSkill,
        cert: CertificationRow,
        verified: Verified,
        source_manifest: SubSkillManifest,
        manifest: dict[str, Any],
        dataset_sha256: str,
        after: dict[str, Any],
    ) -> None:
        attempt = verified.attempt
        if attempt is None:  # decide() only reuses on verified evidence
            raise RuntimeError("reuse without verified evidence")
        now = self.clock()
        data = copy.deepcopy(manifest)
        # the version number is kept: the reused Sub-Skill stays at the version it was certified at
        data["skill_metadata"]["version"] = source_manifest.manifest["skill_metadata"]["version"]
        data["governance"]["versioning"] = copy.deepcopy(
            source_manifest.manifest["governance"]["versioning"]
        )
        evidence = cert.evidence
        manifest_id, attempt_id = uuid7(), uuid7()
        record_block = {
            "code_attempt_id": str(attempt_id),
            "bundle_sha256": _digest(str(evidence["bundle_sha256"])),
            "gates": list(evidence["gates"]),
            "test_run_id": str(evidence["test_run_id"]),
            "sandbox_run_id": str(evidence["sandbox_run_id"]),
            "passed_cases": int(evidence["passed_cases"]),
        }
        try:
            approved = approved_manifest(data, record_block, now, reused_from=str(cert.id))
        except (ValidationError, ValueError, KeyError):
            raise RuntimeError("the reused manifest does not validate") from None
        manifest_row = SubSkillManifestRepository(session, ctx).add(
            SubSkillManifest(
                id=manifest_id,
                sub_skill_id=sub.id,
                build_no=1,
                cycle=0,
                attempt=0,
                manifest=approved.dump(),
                sha256=approved.governance.deterministic_hash,
                runtime_digest=data["sbam_build_record"]["build"]["runtime_image_digest"],
                is_frozen=True,
                created_by=CREATED_BY,
            )
        )
        CodeAttemptRepository(session, ctx).add(
            CodeAttempt(
                id=attempt_id,
                workflow_id=inp.workflow_id,
                sub_skill_id=sub.id,
                manifest_id=manifest_row.id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name=attempt.template_name,
                template_version=attempt.template_version,
                template_sha256=attempt.template_sha256,
                status=CodeAttemptStatus.ASSEMBLED.value,
                bodies=dict(attempt.bodies),
                llm_call_ids=[],
                trace_id=attempt.trace_id,
                findings=[],
                bundle_key=attempt.bundle_key,
                files=dict(attempt.files or {}),
                bundle_sha256=attempt.bundle_sha256,
                black_version=attempt.black_version,
                black_config_sha256=attempt.black_config_sha256,
                created_by=CREATED_BY,
            )
        )
        CertificationRepository(session, ctx).record(
            {
                "level": CertificationLevel.SUB_SKILL.value,
                "subject_id": sub.id,
                "content_sha256": cert.content_sha256,
                "evidence": {
                    **record_block,
                    "dataset_sha256": dataset_sha256 or evidence.get("dataset_sha256", ""),
                    "suite_sha256": evidence.get("suite_sha256", ""),
                    "manifest_sha256": manifest_row.sha256,
                    "source_code_attempt_id": str(attempt.id),
                },
                "certified_at": now,
                "reused_from_id": cert.id,
                "created_by": CREATED_BY,
            }
        )
        self._audit(session, inp, sub, REUSED_ACTION, after)

    # --- the activity ----------------------------------------------------------------------------

    def reuse_all(self, inp: WorkflowRef) -> list[uuid.UUID]:
        """The reused certificates' ids (empty when nothing was carried over)."""
        ctx = TenantContext(inp.tenant_id, inp.project_id)
        reused: list[uuid.UUID] = []
        with self.sessions() as session:
            certs = CertificationRepository(session, ctx)
            for sub in SubSkillRepository(session, ctx).of_workflow(inp.workflow_id):
                if sub.reused_from_id is None or sub.state != SubSkillState.CERTIFIED.value:
                    continue  # not carried over, or already refused and on its way to a rebuild
                done = certs.latest(CertificationLevel.SUB_SKILL.value, sub.id)
                if done is not None and done.reused_from_id is not None:
                    reused.append(done.id)  # a retried activity: the certificate stands
                    continue
                took = self._one(session, ctx, inp, sub)
                session.commit()  # one Sub-Skill at a time: each outcome stands on its own
                if took:
                    found = certs.latest(CertificationLevel.SUB_SKILL.value, sub.id)
                    if found is not None:
                        reused.append(found.id)
        return reused

    @activity.defn(name=STAGE)
    async def run(self, inp: WorkflowRef) -> IdsResult:
        return IdsResult(ids=await asyncio.to_thread(self.reuse_all, inp))


def sql_current_manifest(sessions: sessionmaker[Session]) -> CurrentManifest:
    """The copy's manifest as `build_manifest` would write it now, without storing it."""
    from sbam_workers.build.manifest import (
        ManifestInvalid,
        SqlManifestStore,
        _template,
        build_skill_manifest,
    )

    store = SqlManifestStore(sessions)

    def current(inp: WorkflowRef, sub: SubSkill) -> dict[str, Any] | None:
        stage = BuildStageInput(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            run_id=inp.run_id,
            correlation_id=inp.correlation_id,
            workflow_id=inp.workflow_id,
            sub_skill_id=sub.id,
            build_no=1,
        )
        try:
            source = store.load(stage, _template())
            built, _ = build_skill_manifest(
                source.raw, macro_category=source.macro_category, ctx=source.ctx
            )
        except (ManifestInvalid, ApplicationError):
            return None  # not a reason to stop the run: the Sub-Skill is built, and says why there
        return built.dump()

    return current


def _stage() -> ReuseCertifications:
    from sbam_api.storage.factory import make_backend

    from sbam_workers.settings import WorkerSettings

    storage = make_backend(WorkerSettings())  # type: ignore[call-arg]  # values from the env
    sessions = mirror.sessions()
    return ReuseCertifications(sessions, storage, sql_current_manifest(sessions))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name=STAGE)
async def reuse_certifications(inp: WorkflowRef) -> IdsResult:
    return await _stage().run(inp)
