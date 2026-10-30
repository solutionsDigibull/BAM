"""What one sandbox run needs from the database and storage (E13.2, ADR 0023).

`identify` is the cheap half (three rows): which code attempt, which dataset, which runtime. The
activity claims its `sandbox_runs` row with that, so everything that can go wrong afterwards lands
on a row. `load` is the heavy half: the stored bundle, the Workflow's schema.sql, the dataset with
its files and mocks, and the harness configuration.

Rules worth knowing
  * Nothing is believed before the executor re-checks it: this module reads bytes, the executor
    verifies every hash before any VM exists. What is checked here is what the executor cannot know:
    that the gates passed *for this bundle*, that the schema is the one the manifest names, and that
    the dataset is synthetic and has the scenarios a loop needs.
  * The harness configuration is built from the manifest's render params, the same source the bundle
    was generated from, never from the bundle under test.
  * Every failure of an input is non-retryable `SandboxInputMismatch`: retrying cannot change a row.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository, SubSkillManifestRepository
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.storage.base import StorageBackend
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testgen.service import body_of, files_hashes, mocks_hashes
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.mocks import MockDefinition
from sbam_contracts.sandbox import HarnessConfig
from sbam_contracts.testgen import MIN_CASES, REQUIRED_KINDS, CaseKind
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

from sbam_workers.build.codegen import params_from_manifest
from sbam_workers.build.codegen.bundle import LOOP_SPEC_FILE
from sbam_workers.isatvon.loop_spec import load_loop_spec
from sbam_workers.sandbox.executor import DatasetInput
from sbam_workers.sandbox.inputs import harness_config
from sbam_workers.sandbox.verify import verify_bundle
from sbam_workers.test.testgen import data_files

INPUT_MISMATCH: Final = "SandboxInputMismatch"
PACKAGE: Final = "skill"  # the bundle's modules import each other relatively: they need a package
TOOL_MODULE: Final = f"{PACKAGE}.server"
CODE_GATES: Final = (
    GateName.AST_SYNTAX,
    GateName.AST_SKELETON,
    GateName.AST_CONFORMANCE,
    GateName.AST_DECOUPLING,
    GateName.AST_NO_MANUAL_LOOP,
    GateName.AST_HANDOFF,
    GateName.AST_IMPORTS,
    GateName.AST_SQL_SAFETY,
    GateName.RUFF,
    GateName.BLACK,
    GateName.MYPY,
    GateName.CLAMAV,
    GateName.BANDIT,
)
OPTIONAL_GATES: Final = (
    GateName.IMPORT_RESOLUTION,
)  # not in every build yet: if it ran, it passed

IsatvonOf = Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]]


def mismatch(message: str) -> ApplicationError:
    return ApplicationError(message, type=INPUT_MISMATCH, non_retryable=True)


@dataclass(frozen=True)
class Identity:
    attempt_id: uuid.UUID
    dataset_id: uuid.UUID
    runtime_digest: str


@dataclass(frozen=True)
class Loaded:
    bundle_tar: bytes
    bundle_files: Mapping[str, str]
    bundle_sha256: str
    loop_spec: dict[str, Any]
    harness: HarnessConfig
    handoff_table: str
    schema_sql: bytes
    schema_sql_sha256: str
    dataset: DatasetInput
    data_files: Mapping[str, bytes]
    mocks: list[MockDefinition]


def hex_of(digest: str) -> str:
    return digest.removeprefix("sha256:")


class SqlSandboxInputs:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    @staticmethod
    def _ctx(inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def identify(self, inp: BuildStageInput) -> Identity:
        with self.sessions() as session:
            ctx = self._ctx(inp)
            attempt = CodeAttemptRepository(session, ctx).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            if attempt is None or attempt.workflow_id != inp.workflow_id:
                raise mismatch(f"no code attempt for Sub-Skill {inp.sub_skill_id} in this run")
            if attempt.status != CodeAttemptStatus.ASSEMBLED or attempt.manifest_id is None:
                raise mismatch(f"code attempt {attempt.id} is {attempt.status}, not assembled")
            manifest = SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)
            if manifest is None:
                raise mismatch("the code attempt's manifest is missing")
            dataset = DatasetRepository(session, ctx).latest(inp.sub_skill_id)
            if dataset is None:
                raise mismatch("the Sub-Skill has no test data")
            return Identity(attempt.id, dataset.id, manifest.runtime_digest)

    def load(self, inp: BuildStageInput, identity: Identity, isatvon: Mapping[str, Any]) -> Loaded:
        with self.sessions() as session:
            ctx = self._ctx(inp)
            attempt = CodeAttemptRepository(session, ctx).get(identity.attempt_id)
            manifest_row = (
                None
                if attempt is None or attempt.manifest_id is None
                else SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)
            )
            if attempt is None or manifest_row is None or not attempt.bundle_key:
                raise mismatch("the code attempt, its manifest or its bundle is missing")
            bundle_key, bundle_sha = attempt.bundle_key, attempt.bundle_sha256 or ""
            bundle_files = dict(attempt.files or {})
            self._gates_passed(session, inp, ctx, identity, bundle_sha)
            try:
                manifest = SkillManifest.model_validate(manifest_row.manifest)
            except ValidationError:
                raise mismatch("the stored manifest is not valid") from None
            schema_key, schema_sha = self._schema(session, inp, ctx, manifest)
            dataset = DatasetRepository(session, ctx).get(identity.dataset_id)
            if dataset is None:
                raise mismatch("the dataset is missing")
            rows = CaseRepository(session, ctx).of_dataset(dataset.id)
            cases = [(CaseKind(row.kind), body_of(row)) for row in rows]
            keys, mock_keys = list(dataset.files_keys), list(dataset.mocks_keys)
            dataset_sha, synthetic = dataset.sha256, dataset.synthetic
        self._dataset_is_runnable(cases, synthetic)
        tar = self.storage.get(bundle_key)
        files = verify_bundle(tar, bundle_files, bundle_sha)  # raises HashMismatch: no VM yet
        loop_spec_bytes = files.get(LOOP_SPEC_FILE)
        if loop_spec_bytes is None:
            raise mismatch("the bundle has no loop_spec.json")
        try:
            load_loop_spec(loop_spec_bytes)
            loop_spec: dict[str, Any] = json.loads(loop_spec_bytes)
        except (ValidationError, ValueError):
            raise mismatch("the bundle's loop_spec.json is not a valid loop spec") from None
        params = params_from_manifest(manifest)
        sub_skill = SubSkillModel.model_validate(isatvon).isatvon_definition
        try:
            config = harness_config(params, sub_skill.n_notification.ui_updates)
        except (ValueError, ValidationError) as exc:
            raise mismatch(f"the harness cannot be configured: {type(exc).__name__}") from None
        return Loaded(
            bundle_tar=tar,
            bundle_files=bundle_files,
            bundle_sha256=bundle_sha,
            loop_spec=loop_spec,
            harness=config,
            handoff_table=params["handoff_table"],
            schema_sql=self.storage.get(schema_key),
            schema_sql_sha256=schema_sha,
            dataset=DatasetInput(cases, files_hashes(keys), mocks_hashes(mock_keys), dataset_sha),
            data_files=self._data_files(keys, isatvon),
            mocks=[
                MockDefinition.model_validate_json(self.storage.get(key, verify=True))
                for key in mock_keys
            ],
        )

    def _gates_passed(
        self,
        session: Session,
        inp: BuildStageInput,
        ctx: TenantContext,
        identity: Identity,
        bundle_sha: str,
    ) -> None:
        """Every code gate passed, and each verdict is about *this* bundle."""
        rows = {
            row.gate: row
            for row in GateResultRepository(session, ctx).of_stage(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
        }
        for gate in (*CODE_GATES, *OPTIONAL_GATES):
            row = rows.get(gate.value)
            if row is None and gate in OPTIONAL_GATES:
                continue
            if row is None or row.status != GateStatus.PASSED.value:
                raise mismatch(f"gate {gate.value} has not passed for this attempt")
            if row.code_attempt_id != identity.attempt_id or not _same(
                row.bundle_sha256, bundle_sha
            ):
                raise mismatch(f"gate {gate.value} judged another bundle")

    def _schema(
        self, session: Session, inp: BuildStageInput, ctx: TenantContext, manifest: SkillManifest
    ) -> tuple[str, str]:
        expected = manifest.sbam_build_record.build.schema_sql_sha256
        schema = WorkflowSchemaRepository(session, ctx).latest(
            inp.workflow_id, status=GateStatus.PASSED
        )
        if schema is None or not schema.storage_key or not schema.sha256:
            raise mismatch("the Workflow has no schema.sql that passed its gate")
        if not _same(schema.sha256, expected):
            raise mismatch("the Workflow's schema.sql is not the one the manifest names")
        return schema.storage_key, expected

    @staticmethod
    def _dataset_is_runnable(cases: list[tuple[CaseKind, Any]], synthetic: bool) -> None:
        if not synthetic:
            raise mismatch("the dataset is not marked synthetic")
        kinds = {kind for kind, _ in cases}
        if len(cases) < MIN_CASES or not REQUIRED_KINDS <= kinds:
            raise mismatch("the dataset needs at least 3 cases including PASS_FIRST and ESCALATION")

    def _data_files(self, keys: list[str], isatvon: Mapping[str, Any]) -> dict[str, bytes]:
        """Each stored file under the name the Sub-Skill's FILE reference gave it. The bytes are
        read with their content hash checked; a file no reference explains keeps its hash as its
        name."""
        named = {}
        try:
            for item in data_files(isatvon):
                named[hashlib.sha256(item.content).hexdigest()] = item.name
        except (ValidationError, ValueError):
            named = {}
        out: dict[str, bytes] = {}
        for key in keys:
            digest = key.rsplit("/", 1)[-1]
            out[named.get(digest, digest)] = self.storage.get(key, verify=True)
        return out


def _same(left: str | None, right: str) -> bool:
    return left is not None and hex_of(left) == hex_of(right)
