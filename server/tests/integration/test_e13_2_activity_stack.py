"""E13.2 `run_sandbox_tests` through the real activity: Postgres at head, local object storage, the
real loaders and store, and, where a run must really happen, the hardened `sandbox-runner`
container.

The stage is driven by Temporal's activity environment, the way a worker would, and every claim is
read back from the database: the row, its frozen state, the evidence in storage and the LOOP_TESTS
verdict the corrector reads. Failure paths that need no container use a fake backend so they run in
milliseconds and can fail in ways a real host does (down, slow, wrong).

Needs `make up` (Postgres) and Docker with `skill-runtime:local` for the container tests; not part
of the unit run.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from codegen_cases import ms01_bodies, ms01_loop_spec
from fixtures import fixture_path
from sbam_api.artifacts.models import Artifact, ArtifactKind
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sandbox.repository import SandboxRunRepository
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.sow.models import SowVersion
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.build import TESTS_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.canonical import sha256_of
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.sandbox import FailureCategory, InfraReason, SandboxMode
from sbam_contracts.testgen import CaseBody, CaseKind, dataset_sha256
from sbam_workers.build.codegen import load_template, params_from_manifest, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle
from sbam_workers.sandbox.activity import (
    HASH_MISMATCH,
    SANDBOX_UNAVAILABLE,
    RunSandboxTests,
)
from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxInfraError,
    VmHandle,
    VmInfo,
    VmSpec,
)
from sbam_workers.sandbox.executor import OUTPUTS_PATH
from sbam_workers.sandbox.factory import Sandbox
from sbam_workers.sandbox.load import CODE_GATES, INPUT_MISMATCH, SqlSandboxInputs
from sbam_workers.sandbox.local_backend import LocalDockerBackend
from sbam_workers.sandbox.store import SqlRunStore
from sbam_workers.settings import WorkerSettings
from sqlalchemy import pool, select, text
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from .test_db import url
from .test_e13_2_runner_container import (  # noqa: F401 - agent_url is a fixture
    DIGEST,
    SCHEMA,
    TOKEN,
    agent_url,
    good_cases,
)
from .test_sow_approve_db import TENANT, a_sow

pytestmark = pytest.mark.integration

DB = f"sbam_e132a_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskBackend:
    return LocalDiskBackend(tmp_path)


@dataclass
class World:
    inp: BuildStageInput
    ctx: TenantContext
    isatvon: dict[str, Any]
    attempt_id: uuid.UUID
    bundle_key: str
    sessions: sessionmaker[Session]

    async def isatvon_of(self, _: BuildStageInput) -> dict[str, Any]:
        return self.isatvon


def seed(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    *,
    cases: list[tuple[CaseKind, CaseBody]] | None = None,
    bodies: dict[str, Any] | None = None,
    gates: Sequence[GateName] = CODE_GATES,
    synthetic: bool = True,
) -> World:
    project_id, _ = a_sow(sessions)
    isatvon = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text("utf-8"))
    manifest = json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text("utf-8"))
    chosen = cases if cases is not None else good_cases()
    workflow_id, macro_id, sub_id, attempt_id = uuid7(), uuid7(), uuid7(), uuid7()
    scope = StorageScope(TENANT, project_id, workflow_id)
    schema_key = storage.put(SCHEMA, scope, Kind.CODE, "application/sql")
    manifest["sbam_build_record"]["build"]["schema_sql_sha256"] = "sha256:" + schema_key.sha256
    manifest["sbam_build_record"]["build"]["runtime_image_digest"] = DIGEST
    # The key of fresh state is "the first required input" in the order the database hands the
    # properties back, and JSONB sorts them by length (#624): `otp_payload` would win. One required
    # input leaves no order to get wrong; the recorded bodies read their rows by `mobile_number`.
    manifest["contracts"]["input_schema"]["required"] = ["mobile_number"]
    with sessions() as s:
        version = s.scalars(select(SowVersion).where(SowVersion.project_id == project_id)).one()
        workflow = Workflow(
            id=workflow_id,
            tenant_id=TENANT,
            project_id=project_id,
            sow_version_id=version.id,
            major=1,
            version="1.0.0",
            name="E13.2",
            created_by="SYSTEM",
        )
        workflow.state = "ISATVON_REVIEW"
        s.add(workflow)
        s.flush()
        macro = MacroSkill(
            id=macro_id,
            tenant_id=TENANT,
            project_id=project_id,
            workflow_id=workflow_id,
            sow_version_id=version.id,
            macro_category="MS01_Investor_Registration_Access",
            name="Access",
            description="Access.",
            order=1,
            sow_refs=["M-ACCESS"],
            agent_ref=None,
            validation_errors=[],
        )
        macro.state = "PENDING"
        s.add(macro)
        s.flush()
        sub = SubSkill(
            id=sub_id,
            tenant_id=TENANT,
            project_id=project_id,
            macro_skill_id=macro_id,
            workflow_id=workflow_id,
            skill_id=isatvon["skill_metadata"]["skill_id"],
            skill_name=isatvon["skill_metadata"]["skill_name"],
            order=1,
            isatvon=isatvon,
            isatvon_framework_version="1.4",
            sow_refs=["F-ACCESS-1"],
            validation_errors=[],
            content_sha256=sha256_of(isatvon).removeprefix("sha256:"),
        )
        sub.state = "BUILDING"
        s.add(sub)
        s.flush()
        manifest_id = uuid7()
        s.add(
            SubSkillManifest(
                id=manifest_id,
                tenant_id=TENANT,
                project_id=project_id,
                sub_skill_id=sub_id,
                build_no=1,
                cycle=0,
                attempt=0,
                manifest=manifest,
                sha256="sha256:" + "b" * 64,
                runtime_digest=DIGEST,
                created_by="SYSTEM",
            )
        )
        s.commit()
    # The bundle is generated from the manifest *as the database returns it*, like the pipeline
    # does:
    # JSONB does not keep key order, and the key of fresh state is the first required input.
    with sessions() as s:
        stored_manifest = s.get(SubSkillManifest, manifest_id)
        assert stored_manifest is not None
        params = params_from_manifest(SkillManifest.model_validate(stored_manifest.manifest))
    template = load_template("fastmcp-loopy", "1.0.0")
    assembled = assemble_bundle(
        template,
        render_skeleton(template, params),
        bodies if bodies is not None else ms01_bodies(),
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )
    assert assembled.bundle is not None, assembled.findings
    bundle = assembled.bundle
    bundle_key = storage.put(bundle.tar, scope, Kind.CODE, "application/x-tar").storage_key
    with sessions() as s:
        s.add(
            CodeAttempt(
                id=attempt_id,
                tenant_id=TENANT,
                project_id=project_id,
                workflow_id=workflow_id,
                sub_skill_id=sub_id,
                manifest_id=manifest_id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="fastmcp-loopy",
                template_version="1.0.0",
                template_sha256="0" * 64,
                status="ASSEMBLED",
                bodies={},
                trace_id="t",
                findings=[],
                bundle_key=bundle_key,
                files=dict(bundle.hashes),
                bundle_sha256=bundle.bundle_sha256,
            )
        )
        for gate in gates:
            s.add(
                GateResultRow(
                    id=uuid7(),
                    tenant_id=TENANT,
                    project_id=project_id,
                    sub_skill_id=sub_id,
                    code_attempt_id=attempt_id,
                    gate=gate.value,
                    status=GateStatus.PASSED.value,
                    findings=[],
                    build_no=1,
                    cycle=0,
                    attempt=0,
                    bundle_sha256=bundle.bundle_sha256,
                    created_by="SYSTEM",
                )
            )
        s.add(
            WorkflowSchema(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=project_id,
                workflow_id=workflow_id,
                version=1,
                source="GENERATED",
                storage_key=schema_key.storage_key,
                sha256=schema_key.sha256,
                tables=[],
                status=GateStatus.PASSED.value,
                findings=[],
                created_by="SYSTEM",
            )
        )
        dataset = DatasetRow(
            id=uuid7(),
            tenant_id=TENANT,
            project_id=project_id,
            sub_skill_id=sub_id,
            version=1,
            files_keys=[],
            mocks_keys=[],
            sha256=dataset_sha256(chosen, {}, {}),
            synthetic=synthetic,
            edited_by_fde=False,
            generated_for="x",
            created_by="SYSTEM",
        )
        s.add(dataset)
        s.flush()
        for kind, case in chosen:
            s.add(
                CaseRow(
                    id=uuid7(),
                    tenant_id=TENANT,
                    project_id=project_id,
                    dataset_id=dataset.id,
                    name=case.name,
                    kind=kind.value,
                    input=case.input,
                    initial_state=case.initial_state,
                    state_script=case.state_script,
                    expected_terminal=case.expected_terminal.value,
                    expected_output=case.expected_output,
                    assertions=[],
                    created_by="SYSTEM",
                )
            )
        s.commit()
    inp = BuildStageInput(
        tenant_id=TENANT,
        project_id=project_id,
        run_id=uuid.uuid4(),
        correlation_id="cid-e13-2",
        workflow_id=workflow_id,
        sub_skill_id=sub_id,
        build_no=1,
    )
    return World(inp, TenantContext(TENANT, project_id), isatvon, attempt_id, bundle_key, sessions)


def settings(monkeypatch: pytest.MonkeyPatch) -> WorkerSettings:
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("MOCK_API_URL", raising=False)
    return WorkerSettings()  # type: ignore[call-arg]


def stage(
    world: World,
    storage: LocalDiskBackend,
    backend: Any,
    monkeypatch: pytest.MonkeyPatch,
    mode: SandboxMode = SandboxMode.LOCAL_DOCKER_NONCERT,
) -> RunSandboxTests:
    return RunSandboxTests(
        SqlSandboxInputs(world.sessions, storage),
        SqlRunStore(world.sessions, storage),
        world.isatvon_of,
        settings(monkeypatch),
        sandbox_factory=lambda _: Sandbox(mode, backend, None),
    )


def run_activity(
    activity_stage: RunSandboxTests, inp: BuildStageInput, *, attempt: int = 1, id_: str = "act-1"
) -> Any:
    env = ActivityEnvironment()
    env.info = _info(env, attempt, id_)
    return asyncio.run(env.run(activity_stage.run, inp))


def _info(env: ActivityEnvironment, attempt: int, activity_id: str) -> Any:
    from dataclasses import replace

    return replace(env.info, attempt=attempt, activity_id=activity_id, workflow_id="build-x")


def runs_of(world: World) -> list[SandboxRunRecord]:
    with world.sessions() as s:
        return list(SandboxRunRepository(s, world.ctx).of_attempt(world.attempt_id))  # newest last


def loop_gate(world: World) -> GateResultRow | None:
    with world.sessions() as s:
        return GateResultRepository(s, world.ctx).for_stage(
            world.inp.sub_skill_id, GateName.LOOP_TESTS.value, 1, 0, 0
        )


# --- a backend that fails the way a host does
# -------------------------------------------------------


class FlakyBackend:
    mode = SandboxMode.LOCAL_DOCKER_NONCERT

    def __init__(self, fail_create: SandboxInfraError | None = None) -> None:
        self.fail_create, self.created = fail_create, 0
        self.destroyed: list[str] = []

    async def create(self, spec: VmSpec) -> VmHandle:
        if self.fail_create is not None:
            raise self.fail_create
        self.created += 1
        return VmHandle(f"vm-{self.created}")

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        return DIGEST

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        return None

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        return ExecResult(0, b"", b"")

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        return None

    async def destroy(self, vm_id: str) -> None:
        self.destroyed.append(vm_id)

    async def list_vms(self) -> list[VmInfo]:
        return [VmInfo(f"vm-{self.created}", None)] if self.created else []


# --- the happy path, for real
# -----------------------------------------------------------------------


def test_e13_2_t02_a_real_run_leaves_a_frozen_non_certifying_row_evidence_and_a_passed_gate(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    agent_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = seed(sessions, storage)
    backend = LocalDockerBackend(agent_url, TOKEN)
    result = run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    [row] = runs_of(world)
    assert result.id == row.id and row.status == "FINISHED" and row.is_frozen
    assert row.mode == "LOCAL_DOCKER_NONCERT" and row.failure_category is None
    assert row.exit_code == 0 and row.infra_reason is None and row.vm_id
    assert row.duration_ms is not None and row.duration_ms > 0
    assert row.summary is not None and row.summary["passed"] == 3 and row.summary["failed"] == 0
    for key in (row.outputs_key, row.logs_key):
        assert key and storage.exists(key)
    outputs = json.loads(storage.get(row.outputs_key or "", verify=True))
    assert outputs["mode"] == "LOCAL_DOCKER_NONCERT" and len(outputs["cases"]) == 3
    gate = loop_gate(world)
    assert gate is not None and gate.status == "PASSED" and gate.code_attempt_id == world.attempt_id
    with sessions() as s:
        kinds = {
            a.kind
            for a in s.scalars(
                select(Artifact).where(Artifact.storage_key.in_([row.outputs_key, row.logs_key]))
            )
        }
    assert kinds == {ArtifactKind.SANDBOX_OUTPUTS.value, ArtifactKind.SANDBOX_LOGS.value}


def test_e13_2_the_row_says_the_mode_so_certification_can_refuse_a_local_run(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    agent_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sbam_contracts.sandbox import SandboxRun

    world = seed(sessions, storage)
    run_activity(
        stage(world, storage, LocalDockerBackend(agent_url, TOKEN), monkeypatch), world.inp
    )
    [row] = runs_of(world)
    shown = SandboxRun.model_validate(
        {
            "id": row.id,
            "code_attempt_id": row.code_attempt_id,
            "dataset_id": row.dataset_id,
            "run_no": row.run_no,
            "mode": row.mode,
            "status": row.status,
            "runtime_digest": row.runtime_digest,
            "failure_category": row.failure_category,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
    )
    assert not shown.certifying


def test_e13_2_a_finished_test_outcome_is_a_replay_not_a_second_run(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    agent_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = seed(sessions, storage)
    backend = LocalDockerBackend(agent_url, TOKEN)
    first = run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    again = run_activity(stage(world, storage, backend, monkeypatch), world.inp)  # same activity id
    assert again.id == first.id and len(runs_of(world)) == 1


# --- a bundle that is wrong
# -------------------------------------------------------------------------


def test_e13_2_t05_a_failing_bundle_ends_as_tests_failed_with_findings_for_the_corrector(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    agent_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = ms01_bodies(action_body='raise ValueError("the otp check blew up")\n')
    world = seed(sessions, storage, bodies=broken)
    backend = LocalDockerBackend(agent_url, TOKEN)
    with pytest.raises(ApplicationError) as failed:
        run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    assert failed.value.type == TESTS_FAILED and failed.value.non_retryable
    assert failed.value.details[0] == [str(world.attempt_id)]  # what `SubSkillBuild` reads
    [row] = runs_of(world)
    assert row.failure_category == FailureCategory.RUNTIME_ERROR.value and row.is_frozen
    gate = loop_gate(world)
    assert gate is not None and gate.status == "FAILED" and gate.findings
    assert gate.findings[0]["code"] == "RUNTIME_ERROR" and "the otp check blew up" in str(
        gate.findings
    )
    with sessions() as s:  # the corrector's own query finds it
        failed_gates = GateResultRepository(s, world.ctx).failed_for_pass(
            world.inp.sub_skill_id, 1, 0, 0
        )
    assert [g.gate for g in failed_gates] == ["LOOP_TESTS"]


# --- the sandbox is the problem
# ---------------------------------------------------------------------


def test_e13_2_t03_a_host_that_is_down_is_a_retryable_infrastructure_error_on_a_row(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed(sessions, storage)
    down = FlakyBackend(SandboxInfraError(InfraReason.HOST_UNREACHABLE, "connection refused"))
    for attempt in (1, 2, 3):  # what Temporal's retry policy does
        with pytest.raises(ApplicationError) as caught:
            run_activity(stage(world, storage, down, monkeypatch), world.inp, attempt=attempt)
        assert caught.value.type == SANDBOX_UNAVAILABLE and not caught.value.non_retryable
    rows = runs_of(world)
    assert [r.run_no for r in rows] == [1, 2, 3]
    assert {r.failure_category for r in rows} == {FailureCategory.INFRA_ERROR.value}
    assert {r.infra_reason for r in rows} == {InfraReason.HOST_UNREACHABLE.value}
    assert loop_gate(world) is None  # nothing was learned about the code: no verdict, no correction


def test_e13_2_t04_a_bundle_changed_after_the_gates_creates_no_vm_and_says_why(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed(sessions, storage)
    path = next(Path(storage.root).rglob(world.bundle_key.rsplit("/", 1)[-1]))
    data = bytearray(path.read_bytes())
    data[600] ^= 0xFF  # one flipped byte inside the stored tar
    path.chmod(0o644)  # stored objects are read-only; whoever has the disk is not stopped by that
    path.write_bytes(bytes(data))
    backend = FlakyBackend()
    with pytest.raises(ApplicationError) as caught:
        run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    assert caught.value.non_retryable and caught.value.type == HASH_MISMATCH
    assert backend.created == 0  # no VM was ever asked for
    [row] = runs_of(world)
    assert row.failure_category == "INFRA_ERROR" and row.infra_reason == "HASH_MISMATCH"


def test_e13_2_a_gate_that_has_not_passed_is_refused_before_anything_runs(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed(sessions, storage, gates=CODE_GATES[:-1])  # BANDIT never ran
    backend = FlakyBackend()
    with pytest.raises(ApplicationError) as caught:
        run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable
    assert backend.created == 0
    [row] = runs_of(world)
    assert row.infra_reason == InfraReason.INPUT_MISMATCH.value


def test_e13_2_a_dataset_without_the_loops_scenarios_is_refused(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed(sessions, storage, cases=good_cases()[:1])  # one case, no ESCALATION
    with pytest.raises(ApplicationError) as caught:
        run_activity(stage(world, storage, FlakyBackend(), monkeypatch), world.inp)
    assert caught.value.type == INPUT_MISMATCH


def test_e13_2_nothing_is_run_without_a_configured_sandbox(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sbam_workers.sandbox.factory import make_sandbox

    world = seed(sessions, storage)
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    unconfigured = RunSandboxTests(
        SqlSandboxInputs(sessions, storage),
        SqlRunStore(sessions, storage),
        world.isatvon_of,
        settings(monkeypatch),
        sandbox_factory=make_sandbox,
    )
    with pytest.raises(ApplicationError) as caught:
        run_activity(unconfigured, world.inp)
    assert caught.value.type == SANDBOX_UNAVAILABLE and not caught.value.non_retryable
    assert runs_of(world) == []  # no mode, so no row could say which one it was


# --- a worker that died
# ------------------------------------------------------------------------------


def test_e13_2_t07_a_retry_after_a_dead_worker_supersedes_the_stale_row_and_leaves_one_live_run(
    sessions: sessionmaker[Session],
    storage: LocalDiskBackend,
    agent_url: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = seed(sessions, storage)
    store = SqlRunStore(sessions, storage)
    from sbam_workers.sandbox.load import Identity

    identity = SqlSandboxInputs(sessions, storage).identify(world.inp)
    assert isinstance(identity, Identity)
    stale = store.claim(
        world.inp, identity, SandboxMode.LOCAL_DOCKER_NONCERT,
        workflow_id="build-x", activity_id="act-1", activity_attempt=1,
    )  # fmt: skip  # the first attempt claimed its row, started a VM, and its worker was killed
    store.record_vm(world.inp, stale.run_id, "vm-leaked")
    result = run_activity(
        stage(world, storage, LocalDockerBackend(agent_url, TOKEN), monkeypatch),
        world.inp,
        attempt=2,
    )
    rows = runs_of(world)
    assert [r.run_no for r in rows] == [1, 2]
    assert rows[0].infra_reason == InfraReason.SUPERSEDED.value and rows[0].vm_id == "vm-leaked"
    assert rows[1].id == result.id and rows[1].failure_category is None
    live = [r for r in rows if r.status == "STARTED"]
    assert live == []  # exactly one completed row with a test outcome, none left running


def test_e13_2_a_run_that_reports_nothing_is_not_a_pass(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host that answers every call but whose runner wrote no outputs must never certify
    anything."""
    world = seed(sessions, storage)
    backend = FlakyBackend()
    assert OUTPUTS_PATH.endswith("outputs.json")
    with pytest.raises(ApplicationError) as caught:
        run_activity(stage(world, storage, backend, monkeypatch), world.inp)
    assert caught.value.type == SANDBOX_UNAVAILABLE  # retryable: it is not a verdict on the code
    [row] = runs_of(world)
    assert row.failure_category == "INFRA_ERROR" and row.infra_reason == "RUNNER_FAILED"
    assert backend.destroyed == ["vm-1"]  # and the VM it made is gone
    assert loop_gate(world) is None


# --- a long run, and a cancelled one ------------------------------------------------------------


class SlowBackend(FlakyBackend):
    """`exec` takes `delay` seconds, or forever."""

    def __init__(self, delay: float | None) -> None:
        super().__init__()
        self.delay = delay

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        await asyncio.sleep(self.delay if self.delay is not None else 3600)
        return ExecResult(0, b"", b"")


def test_e13_2_a_long_run_heartbeats_so_temporal_sees_a_dead_worker_early(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sbam_workers.sandbox.activity as module

    monkeypatch.setattr(module, "HEARTBEAT_SECONDS", 0.05)
    world = seed(sessions, storage)
    beats: list[Any] = []
    env = ActivityEnvironment()
    env.info = _info(env, 1, "act-hb")
    env.on_heartbeat = lambda *details: beats.append(details)
    backend = SlowBackend(delay=0.4)
    with pytest.raises(ApplicationError):  # the fake runner writes no outputs: infrastructure
        asyncio.run(env.run(stage(world, storage, backend, monkeypatch).run, world.inp))
    assert len(beats) >= 3 and beats[0] == (str(runs_of(world)[0].id),)
    assert backend.destroyed == ["vm-1"]


def test_e13_2_a_cancelled_activity_destroys_its_vm_and_closes_its_row(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = seed(sessions, storage)
    backend = SlowBackend(delay=None)  # the runner never answers
    env = ActivityEnvironment()
    env.info = _info(env, 1, "act-cancel")

    async def go() -> None:
        task = asyncio.ensure_future(
            env.run(stage(world, storage, backend, monkeypatch).run, world.inp)
        )
        for _ in range(200):  # until the VM exists
            await asyncio.sleep(0.05)
            if backend.created:
                break
        env.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert backend.destroyed == ["vm-1"]  # destroyed in `finally`, even on a cancel
    [row] = runs_of(world)
    assert row.infra_reason == InfraReason.CANCELLED.value and row.is_frozen
    assert row.vm_id == "vm-1"  # recorded the moment it existed, so the reaper could find it


def test_e13_2_a_late_worker_cannot_overwrite_the_row_a_newer_attempt_closed(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sbam_api.db.repository import RowFrozen
    from sbam_workers.sandbox.load import Identity

    world = seed(sessions, storage)
    store = SqlRunStore(sessions, storage)
    identity = SqlSandboxInputs(sessions, storage).identify(world.inp)
    assert isinstance(identity, Identity)
    stale = store.claim(
        world.inp, identity, SandboxMode.LOCAL_DOCKER_NONCERT,
        workflow_id="build-x", activity_id="act-1", activity_attempt=1,
    )  # fmt: skip
    store.claim(  # the retry takes over and closes the stale row as SUPERSEDED
        world.inp, identity, SandboxMode.LOCAL_DOCKER_NONCERT,
        workflow_id="build-x", activity_id="act-1", activity_attempt=2,
    )  # fmt: skip
    with pytest.raises(RowFrozen):  # the stalled worker wakes up and tries to report a pass
        store.finish(
            world.inp, identity, stale.run_id, category=None, reason=None, exit_code=0,
            vm_id="vm-late", duration_ms=1, evidence=None, classified=None,
            bundle_sha256=None, dataset_sha256=None,
        )  # fmt: skip
    first = runs_of(world)[0]
    assert (
        first.infra_reason == InfraReason.SUPERSEDED.value
        and first.failure_category == "INFRA_ERROR"
    )
