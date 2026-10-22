"""The `assemble` build stage (E11.3): bodies + skeleton -> Black -> stored, hashed bundle.

Idempotency (TD §13.4): the stage key `(sub_skill_id, build_no, cycle, attempt)` names the
`code_attempts` row `generate_bodies` wrote. Assembly moves it to ASSEMBLED (bundle stored, indexed
in `artifacts`) or ASSEMBLY_FAILED (an AST_SYNTAX finding -> the correction loop). A retry finds
the final status and returns or re-raises it; storage is content-addressed, so a retry after the
upload but before the row update stores nothing new and finds the same `artifacts` row.

A storage failure (e.g. disk full) leaves the row untouched, counts
`code_bundle_storage_failures_total` for the operator alert and propagates as a retryable error.

Not registered yet, like `GenerateBodies`: whoever wires the build stages (#107) registers
`Assemble(...).run` with real `inputs` and `storage`; `stubs.assemble` stays registered meanwhile.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics
from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import (
    Kind,
    StorageBackend,
    StorageScope,
    StorageUnavailable,
    StoredObject,
)
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.pipeline import IdResult
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.codegen.bundle import (
    BLACK_CONFIG_SHA256,
    BLACK_VERSION,
    Bundle,
    SyntaxFinding,
    assemble_bundle,
)
from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.codegen.store import LoadedTemplate

STAGE = "assemble"
BUNDLE_CONTENT_TYPE = "application/x-tar"
INPUT_MISMATCH = "ASSEMBLY_INPUT_MISMATCH"

_storage_failures = metrics.get_meter("sbam_workers.build").create_counter(
    "code_bundle_storage_failures_total",
    description="Code bundles that could not be stored (E11.3); alert the operator",
)


@dataclass(frozen=True)
class AssembleInputs:
    """What the earlier stages produced for this build: template, params, skeleton, loop spec."""

    template: LoadedTemplate
    params: Mapping[str, Any]
    skeleton: RenderedSkeleton
    loop_spec: bytes  # RFC 8785 canonical loop_spec.json (E8.x)
    manifest_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AttemptRow:
    id: uuid.UUID
    status: str
    bodies: Mapping[str, Any]
    template: tuple[str, str, str]  # name, version, sha256
    manifest_id: uuid.UUID | None


class SqlBundleStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _ctx(self, inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def find(self, inp: BuildStageInput) -> AttemptRow | None:
        with self.sessions() as session:
            row = CodeAttemptRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            if row is None:
                return None
            return AttemptRow(
                row.id,
                row.status,
                dict(row.bodies),
                (row.template_name, row.template_version, row.template_sha256),
                row.manifest_id,
            )

    def assembled(
        self, inp: BuildStageInput, attempt_id: uuid.UUID, stored: StoredObject, bundle: Bundle
    ) -> None:
        """Index the stored tar and mark the attempt ASSEMBLED, in one transaction."""
        with self.sessions() as session:
            ArtifactRepository(session, self._ctx(inp)).index(
                ArtifactKind.CODE_BUNDLE, stored, "sbam-worker"
            )
            repo = CodeAttemptRepository(session, self._ctx(inp))
            row = repo.get(attempt_id)
            if row is None:  # deleted between find and now: let Temporal retry and re-find
                raise RuntimeError(f"code_attempts {attempt_id} disappeared during {STAGE}")
            version = row.row_version
            row.status = CodeAttemptStatus.ASSEMBLED
            row.bundle_key = stored.storage_key
            row.files = dict(bundle.hashes)
            row.bundle_sha256 = bundle.bundle_sha256
            row.black_version = BLACK_VERSION
            row.black_config_sha256 = BLACK_CONFIG_SHA256
            repo.update(row, version)
            session.commit()

    def failed(
        self, inp: BuildStageInput, attempt_id: uuid.UUID, findings: tuple[SyntaxFinding, ...]
    ) -> None:
        with self.sessions() as session:
            repo = CodeAttemptRepository(session, self._ctx(inp))
            row = repo.get(attempt_id)
            if row is None:
                raise RuntimeError(f"code_attempts {attempt_id} disappeared during {STAGE}")
            version = row.row_version
            row.status = CodeAttemptStatus.ASSEMBLY_FAILED
            row.findings = [*row.findings, *(f.as_dict() for f in findings)]
            row.black_version = BLACK_VERSION
            row.black_config_sha256 = BLACK_CONFIG_SHA256
            repo.update(row, version)
            session.commit()


def assembly_failed(attempt_id: uuid.UUID) -> ApplicationError:
    """GateFailed -> the correction loop; details[0] = ids of the records holding the findings."""
    return ApplicationError(
        "assembled code does not parse",
        [str(attempt_id)],
        type=GATE_FAILED,
        non_retryable=True,
    )


def _check_inputs(row: AttemptRow, given: AssembleInputs) -> None:
    """The skeleton must come from the template and manifest the bodies were generated for."""
    t = given.template
    if row.template != (t.name, t.version, t.sha256) or row.manifest_id != given.manifest_id:
        raise ApplicationError(
            "skeleton does not match the code attempt's template or manifest",
            [str(row.id)],
            type=INPUT_MISMATCH,
            non_retryable=True,
        )


class Assemble:
    """`assemble` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[BuildStageInput], Awaitable[AssembleInputs]],
        storage: StorageBackend,
        attempts: SqlBundleStore,
    ) -> None:
        self.inputs = inputs
        self.storage = storage
        self.attempts = attempts

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        row = self.attempts.find(inp)
        if row is None:  # generate_bodies always runs first; a missing row is a wiring bug
            raise ApplicationError(
                f"no code_attempts row for {inp.sub_skill_id} build {inp.build_no}",
                type=INPUT_MISMATCH,
                non_retryable=True,
            )
        if row.status == CodeAttemptStatus.ASSEMBLED:
            return IdResult(id=row.id)
        if row.status == CodeAttemptStatus.ASSEMBLY_FAILED:
            raise assembly_failed(row.id)
        if row.status != CodeAttemptStatus.BODIES_GENERATED:
            raise ApplicationError(
                f"code_attempts {row.id} is {row.status}, not ready to assemble",
                [str(row.id)],
                type=INPUT_MISMATCH,
                non_retryable=True,
            )
        given = await self.inputs(inp)
        _check_inputs(row, given)
        out = assemble_bundle(
            given.template,
            given.skeleton,
            row.bodies,
            given.params.get("fresh_state", []),
            given.loop_spec,
        )
        if out.bundle is None:
            self.attempts.failed(inp, row.id, out.findings)
            activity.logger.info(
                "assembled code does not parse", extra={"files": [f.file for f in out.findings]}
            )
            raise assembly_failed(row.id)
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        try:
            stored = self.storage.put(out.bundle.tar, scope, Kind.CODE, BUNDLE_CONTENT_TYPE)
        except StorageUnavailable:
            _storage_failures.add(1)
            raise  # retryable: the row stays BODIES_GENERATED
        self.attempts.assembled(inp, row.id, stored, out.bundle)
        return IdResult(id=row.id)
