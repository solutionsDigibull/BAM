"""E13.6-T02 and E14.1-T01, the two CERTIFYING cases: MS01.1 run in CubeSandbox (#127, #129).

A local-docker run is NON_CERTIFYING by design, so these two cases can only be shown on a host with
a CubeSandbox (KVM) control plane. Everything below is the product's code; nothing is faked:

* the bundle is generated from the MS01.1 fixture and its manifest (`assemble_bundle`, as
  `test_e13_2_activity_stack.seed` does), stored, gated (gate rows seeded PASSED on the real bundle
  hash, as `certify_seed` does: the gates are not what these cases test);
* `run_sandbox_tests` is the real `RunSandboxTests` activity with the real sandbox factory
  (`make_sandbox`), so the adapter is `CubeSandboxBackend` and the egress reader is the configured
  one, and the real `StageJudge`, so the `test_runs` row is the judge's own (`record_run`);
* the badge is read through `testruns.service.loop_tests_badge` and through
  `builds.dashboard.builds`, the function behind `GET /projects/{p}/workflows/{w}/builds`;
* certification is the real `CertifySubSkill`, and CERTIFIED is reached through the real state
  machine (`apply_transition`, `tests_passed`), as `SubSkillBuild` does after the activity.

The pytest process is the test worker: it reaches CubeAPI and CubeProxy itself (loopback on the
host) and the stack's Postgres on its published port. `worker-test` and the compose overlay are not
used.

They SKIP unless `SANDBOX_MODE=CUBESANDBOX` is set in this process's environment (CI's compose-smoke
has no CubeSandbox). Once it is set they never skip: a missing setting, a stack that is down, an
unreachable host or any wrong answer FAILS. Runbook: docs/runbooks/cubesandbox-certifying-tests.md.
"""

import asyncio
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_bodies, ms01_loop_spec
from fixtures import fixture_path
from sbam_api.builds import dashboard
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationRow
from sbam_api.core.auth import ROLE_FDE, CurrentUser
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sandbox.repository import SandboxRunRepository
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testruns.repository import TestRunRepository
from sbam_api.testruns.service import loop_tests_badge
from sbam_api.workflows.models import Workflow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.canonical import sha256_of
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.promptfoo import LoopTestsBadge, TestRunState
from sbam_contracts.sandbox import SandboxMode
from sbam_contracts.state_machines import Actor
from sbam_contracts.testgen import dataset_sha256
from sbam_workers.build.codegen import load_template, params_from_manifest, render_skeleton
from sbam_workers.build.codegen.bundle import assemble_bundle
from sbam_workers.certify.checklist import REQUIRED_GATES
from sbam_workers.certify.subskill import CertifySubSkill
from sbam_workers.sandbox.activity import RunSandboxTests
from sbam_workers.sandbox.factory import problem
from sbam_workers.sandbox.judging import StageJudge
from sbam_workers.sandbox.load import SqlSandboxInputs
from sbam_workers.sandbox.store import SqlRunStore
from sbam_workers.settings import WorkerSettings
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from .certify_seed import ALL_GATES, _approved_sow, draft_manifest, isatvon_of, scratch_db
from .conftest import is_open, port
from .test_e13_2_runner_container import SCHEMA, good_cases

# --- opting in --------------------------------------------------------------------------------

# The skill-runtime digest the host's READY template was built from (its `/etc/sbam-runtime`).
# The manifest names it, the adapter resolves the template alias `sbam-rt-<56 hex>` from it, and the
# VM's file must equal it, so it cannot be guessed here.
DIGEST_VAR = "SBAM_CUBE_RUNTIME_DIGEST"
REQUIRED_VARS = (
    "SANDBOX_MODE=CUBESANDBOX",
    "CUBE_API_URL",
    "CUBE_PROXY_NODE_IP",
    "SANDBOX_EGRESS_URL and SANDBOX_EGRESS_TOKEN (or SANDBOX_EGRESS_LOG_PATH)",
    DIGEST_VAR,
)
RUNBOOK = "docs/runbooks/cubesandbox-certifying-tests.md"
OPTED_IN = os.environ.get("SANDBOX_MODE", "").strip() == SandboxMode.CUBESANDBOX.value
SKIP_REASON = (
    "CubeSandbox is not configured (certifying run, mode CUBESANDBOX): set "
    + ", ".join(REQUIRED_VARS)
    + f"; optional CUBE_PROXY_PORT_HTTP, CUBE_SANDBOX_DOMAIN, CUBE_API_KEY. See {RUNBOOK}"
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not OPTED_IN, reason=SKIP_REASON)]

BADGE_POLL_S = 5.0  # the Build & Test grid polls at most every 5 s (E13.6 output checks)
FROZEN_SQLSTATE = "SB423"


@pytest.fixture(scope="module", autouse=True)
def _stack_up() -> None:
    """Replaces conftest's skip: once a CubeSandbox run is asked for, a stack that is down is a
    test failure (`CubeRun.get` checks it), never a skip."""


def cube_digest() -> str:
    digest = os.environ.get(DIGEST_VAR, "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        pytest.fail(f"{DIGEST_VAR} must be the full 'sha256:<64 hex>' of the host's template")
    return digest


def worker_settings() -> WorkerSettings:
    """The worker's own settings from this process's environment, as `worker-test` reads them."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TEMPORAL_HOST", "temporal:7233")  # required by the model; never dialled here
        mp.setenv("DATABASE_URL", "sqlite://")  # the run mirror; the stage gets its sessions
        mp.delenv("MOCK_API_URL", raising=False)  # MS01.1's cases call no outside service
        settings = WorkerSettings()  # type: ignore[call-arg]
    why = problem(settings)
    if settings.sandbox_mode is not SandboxMode.CUBESANDBOX or why is not None:
        pytest.fail(f"the CubeSandbox adapter is not fully configured: {why}")
    return settings


# --- one build on a real bundle ---------------------------------------------------------------


@dataclass(frozen=True)
class CubeBuild:
    sessions: sessionmaker[Session]
    storage: LocalDiskBackend
    inp: BuildStageInput
    ctx: TenantContext
    manifest_id: uuid.UUID
    attempt_id: uuid.UUID
    bundle_sha256: str
    digest: str


def seed_cube_build(
    sessions: sessionmaker[Session], storage: LocalDiskBackend, digest: str
) -> CubeBuild:
    """MS01.1 on an approved SOW: a DRAFT manifest naming the host's runtime, the bundle generated
    from it and stored, every gate PASSED on that bundle, the Workflow's schema.sql and a synthetic
    dataset with PASS_FIRST, ESCALATION and NO_OP. The Sub-Skill is BUILDING; no sandbox run yet."""
    tenant = uuid.uuid4()  # a tenant of its own
    isatvon: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text("utf-8")
    )
    workflow_id, macro_id, sub_id, attempt_id, manifest_id = (uuid7() for _ in range(5))
    cases = good_cases()
    with sessions() as s:
        project_id, version = _approved_sow(s, tenant)
        scope = StorageScope(tenant, project_id, workflow_id)
        schema_key = storage.put(SCHEMA, scope, Kind.CODE, "application/sql")
        data = draft_manifest(version)
        build = data["sbam_build_record"]["build"]
        build["schema_sql_sha256"] = "sha256:" + schema_key.sha256
        build["runtime_image_digest"] = digest
        # One required input, as test_e13_2_activity_stack does (#624): the key of fresh state is
        # the first required input in the order JSONB hands the properties back.
        data["contracts"]["input_schema"]["required"] = ["mobile_number"]
        manifest = SkillManifest.sealed(data)
        workflow = Workflow(
            id=workflow_id,
            tenant_id=tenant,
            project_id=project_id,
            sow_version_id=version.id,
            major=1,
            version="1.0.0",
            name="cube",
            created_by="SYSTEM",
        )
        workflow.state = "ISATVON_REVIEW"
        s.add(workflow)
        s.flush()
        macro = MacroSkill(
            id=macro_id,
            tenant_id=tenant,
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
            tenant_id=tenant,
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
        s.add(
            SubSkillManifest(
                id=manifest_id,
                tenant_id=tenant,
                project_id=project_id,
                sub_skill_id=sub_id,
                build_no=1,
                cycle=0,
                attempt=0,
                manifest=manifest.dump(),
                sha256=manifest.governance.deterministic_hash,
                runtime_digest=digest,
                created_by="SYSTEM",
            )
        )
        s.commit()
    # The bundle is generated from the manifest as the database returns it, like the pipeline does.
    with sessions() as s:
        stored = s.get(SubSkillManifest, manifest_id)
        assert stored is not None
        params = params_from_manifest(SkillManifest.model_validate(stored.manifest))
    template = load_template("fastmcp-loopy", "1.0.1")
    assembled = assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies(),
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
                tenant_id=tenant,
                project_id=project_id,
                workflow_id=workflow_id,
                sub_skill_id=sub_id,
                manifest_id=manifest_id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="fastmcp-loopy",
                template_version="1.0.1",
                template_sha256="0" * 64,
                status="ASSEMBLED",
                bodies={},
                trace_id="t",
                findings=[],
                bundle_key=bundle_key,
                files=dict(bundle.hashes),
                bundle_sha256=bundle.bundle_sha256,
                created_by="SYSTEM",
            )
        )
        s.flush()
        for gate in ALL_GATES:  # every gate certification needs, plus BANDIT, on this bundle
            s.add(
                GateResultRow(
                    id=uuid7(),
                    tenant_id=tenant,
                    project_id=project_id,
                    sub_skill_id=sub_id,
                    code_attempt_id=attempt_id,
                    gate=gate.value,
                    status="PASSED",
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
                tenant_id=tenant,
                project_id=project_id,
                workflow_id=workflow_id,
                version=1,
                source="GENERATED",
                storage_key=schema_key.storage_key,
                sha256=schema_key.sha256,
                tables=[],
                status="PASSED",
                findings=[],
                created_by="SYSTEM",
            )
        )
        dataset = DatasetRow(
            id=uuid7(),
            tenant_id=tenant,
            project_id=project_id,
            sub_skill_id=sub_id,
            version=1,
            files_keys=[],
            mocks_keys=[],
            sha256=dataset_sha256(cases, {}, {}),
            synthetic=True,
            edited_by_fde=False,
            generated_for="x",
            created_by="SYSTEM",
        )
        s.add(dataset)
        s.flush()
        for kind, case in cases:
            s.add(
                CaseRow(
                    id=uuid7(),
                    tenant_id=tenant,
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
        tenant_id=tenant,
        project_id=project_id,
        run_id=uuid.uuid4(),
        correlation_id=f"cid-cube-{uuid.uuid4().hex[:8]}",
        workflow_id=workflow_id,
        sub_skill_id=sub_id,
        build_no=1,
    )
    return CubeBuild(
        sessions,
        storage,
        inp,
        TenantContext(tenant, project_id),
        manifest_id,
        attempt_id,
        bundle.bundle_sha256,
        digest,
    )


def transition(build: CubeBuild, event: str) -> Any:
    """One Sub-Skill transition through the state machine, as `apply_pipeline_transition` does."""
    with build.sessions() as s:
        result = apply_transition(
            s,
            build.ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=build.inp.sub_skill_id,
                event=event,
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=build.inp.correlation_id,
                idempotency_key=f"{build.inp.run_id}:{event}",
            ),
        )
        s.commit()
        return result


def run_sandbox_tests(build: CubeBuild, settings: WorkerSettings) -> uuid.UUID:
    """The real stage, wired as `sandbox/activity._stage` wires it, inside Temporal's activity
    environment. No rubric grader: a suite loaded from the database asks none (`load_suite`)."""
    stage = RunSandboxTests(
        SqlSandboxInputs(build.sessions, build.storage),
        SqlRunStore(build.sessions, build.storage),
        isatvon_of,
        settings,  # the default sandbox_factory is the real `make_sandbox`
        judge=StageJudge(build.sessions, isatvon_of, None, build.storage),
    )
    env = ActivityEnvironment()
    env.info = replace(env.info, attempt=1, activity_id="act-cube", workflow_id="build-cube")
    return asyncio.run(env.run(stage.run, build.inp)).id


class CubeRun:
    """The one CubeSandbox run both cases read, made by the first test that asks for it. A failure
    is kept and reported by every test that needs the run (as a failure, never a skip)."""

    def __init__(self, scratch: ExitStack, storage: LocalDiskBackend) -> None:
        self.scratch, self.storage = scratch, storage
        self._done: tuple[CubeBuild, uuid.UUID] | None = None
        self._failed: str | None = None

    def get(self) -> tuple[CubeBuild, uuid.UUID]:
        if self._failed is not None:
            pytest.fail(f"the CubeSandbox run failed: {self._failed}")
        if self._done is None:
            try:
                self._done = self._run()
            except ApplicationError as exc:
                self._failed = f"{exc.type}: {exc} (details {exc.details})"
                pytest.fail(f"the CubeSandbox run failed: {self._failed}")
        return self._done

    def _run(self) -> tuple[CubeBuild, uuid.UUID]:
        if not is_open(port("API_PORT", 8000)):
            pytest.fail("Compose stack is not running (make up): Postgres is needed for this run")
        settings = worker_settings()
        digest = cube_digest()
        sessions, _ = self.scratch.enter_context(scratch_db("sbam_cube"))  # dropped at teardown
        build = seed_cube_build(sessions, self.storage, digest)
        moved = transition(build, "tests_started")  # SubSkillBuild applies it before the stage
        assert moved.to_state == "TESTING", moved
        try:
            run_id = run_sandbox_tests(build, settings)
        except ApplicationError:
            for row in sandbox_runs(build):  # what the row says, for the host owner
                print(
                    "sandbox_run",
                    row.run_no,
                    row.mode,
                    row.status,
                    row.failure_category,
                    row.infra_reason,
                    row.vm_id,
                )
            raise
        return build, run_id


@pytest.fixture(scope="module")
def cube(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CubeRun]:
    with ExitStack() as scratch:  # the scratch database is made by the first test, inside it
        yield CubeRun(scratch, LocalDiskBackend(Path(tmp_path_factory.mktemp("cube-storage"))))


def sandbox_runs(build: CubeBuild) -> list[SandboxRunRecord]:
    with build.sessions() as s:
        return list(SandboxRunRepository(s, build.ctx).of_attempt(build.attempt_id))


def fde(build: CubeBuild) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        keycloak_sub="cube-fde",
        tenant_id=build.ctx.tenant_id,
        roles=frozenset({ROLE_FDE}),
        assigned_project_ids=frozenset({build.inp.project_id}),
    )


# --- E13.6-T02 --------------------------------------------------------------------------------


def test_e13_6_t02_happy_3_of_3_badge_pass_cubesandbox(cube: CubeRun) -> None:
    """MS01.1 with PASS_FIRST, ESCALATION and NO_OP, all correct, run in CubeSandbox: the run is
    CUBESANDBOX, the judge's test_runs row is PASS 3/3, and the grid's badge says PASS 3/3 at the
    first read after the insert (within one 5 s poll)."""
    build, run_id = cube.get()

    [row] = sandbox_runs(build)
    assert row.id == run_id
    assert row.mode == SandboxMode.CUBESANDBOX.value, row.mode
    assert row.status == "FINISHED" and row.is_frozen, (row.status, row.is_frozen)
    assert row.failure_category is None and row.infra_reason is None, (
        row.failure_category,
        row.infra_reason,
    )
    assert row.exit_code == 0 and row.vm_id, (row.exit_code, row.vm_id)
    assert row.runtime_digest == build.digest  # the VM's /etc/sbam-runtime, checked in the VM
    assert row.summary is not None and row.summary["passed"] == 3, row.summary
    outputs = json.loads(build.storage.get(row.outputs_key or "", verify=True))
    assert outputs["mode"] == SandboxMode.CUBESANDBOX.value and len(outputs["cases"]) == 3

    with build.sessions() as s:
        test_run = TestRunRepository(s, build.ctx).for_sandbox_run(run_id)
        gate = GateResultRepository(s, build.ctx).for_stage(
            build.inp.sub_skill_id, GateName.LOOP_TESTS.value, 1, 0, 0
        )
        kinds = {
            c.name: c.kind
            for c in s.scalars(select(CaseRow).where(CaseRow.tenant_id == build.ctx.tenant_id))
        }
    assert test_run is not None, "the judge wrote no test_runs row"
    assert (test_run.total, test_run.passed, test_run.failed) == (3, 3, 0), test_run.results
    assert test_run.state == TestRunState.PASS.value and not test_run.non_certifying
    assert test_run.code_attempt_id == build.attempt_id
    assert sorted(kinds.values()) == ["ESCALATION", "NO_OP", "PASS_FIRST"]
    assert {r["case"] for r in test_run.results} == set(kinds), test_run.results
    assert all(r["passed"] for r in test_run.results), test_run.results
    assert gate is not None and gate.status == "PASSED", gate

    want = LoopTestsBadge(state=TestRunState.PASS, passed=3, total=3)
    with build.sessions() as s:
        assert loop_tests_badge(s, build.ctx, build.inp.sub_skill_id) == want
        grid = dashboard.builds(s, fde(build), build.inp.project_id, build.inp.workflow_id)
    read_at = datetime.now(UTC)
    [cell] = [r for r in grid.sub_skills if r.sub_skill_id == build.inp.sub_skill_id]
    inserted = test_run.created_at
    if inserted.tzinfo is None:
        inserted = inserted.replace(tzinfo=UTC)
    lag = (read_at - inserted).total_seconds()
    print(
        "E13.6-T02",
        json.dumps(
            {
                "sandbox_run_id": str(run_id),
                "mode": row.mode,
                "vm_id": row.vm_id,
                "runtime_digest": row.runtime_digest,
                "test_run_id": str(test_run.id),
                "total": test_run.total,
                "passed": test_run.passed,
                "state": test_run.state,
                "grid_loop_tests": (
                    None if cell.loop_tests is None else cell.loop_tests.model_dump()
                ),
                "badge_read_after_insert_s": round(lag, 3),
            }
        ),
    )
    assert cell.loop_tests == want, (
        "GET /projects/{p}/workflows/{w}/builds does not show the Loop tests badge "
        f"(BuildRow.loop_tests = {cell.loop_tests!r}, want PASS 3/3)"
    )
    assert lag <= BADGE_POLL_S, f"the grid showed the badge {lag:.1f}s after the test_runs insert"


# --- E14.1-T01 --------------------------------------------------------------------------------


def test_e14_1_t01_full_evidence_certifies_cubesandbox(cube: CubeRun) -> None:
    """Every gate PASSED and a CUBESANDBOX 3/3 run, all on the same bundle hash: the real
    certify stage writes the certificate, seals the manifest APPROVED and immutable (frozen in
    Postgres), and the state machine moves the Sub-Skill to CERTIFIED."""
    build, run_id = cube.get()
    with build.sessions() as s:
        test_run = TestRunRepository(s, build.ctx).of_code_attempt(build.attempt_id)
    assert test_run is not None and test_run.sandbox_run_id == run_id

    stage = CertifySubSkill(build.sessions, isatvon_of)
    cert_id = asyncio.run(stage.run(build.inp)).id

    with build.sessions() as s:
        certs = list(
            s.scalars(
                select(CertificationRow).where(
                    CertificationRow.tenant_id == build.ctx.tenant_id,
                    CertificationRow.subject_id == build.inp.sub_skill_id,
                )
            )
        )
        sealed = s.get(SubSkillManifest, build.manifest_id)
        assert sealed is not None
    [cert] = certs
    assert cert.id == cert_id and cert.level == "SUB_SKILL" and cert.is_frozen
    assert cert.content_sha256 == "sha256:" + build.bundle_sha256
    evidence = cert.evidence
    assert evidence["code_attempt_id"] == str(build.attempt_id)
    assert evidence["bundle_sha256"] == build.bundle_sha256
    assert evidence["sandbox_run_id"] == str(run_id)
    assert evidence["test_run_id"] == str(test_run.id)
    assert evidence["suite_sha256"] == test_run.suite_sha256
    assert {g["gate"] for g in evidence["gates"]} >= {g.value for g in REQUIRED_GATES}
    assert all(g["status"] == "PASSED" for g in evidence["gates"]), evidence["gates"]
    [run] = sandbox_runs(build)
    assert run.mode == SandboxMode.CUBESANDBOX.value  # the certified run is the certifying one

    approved = SkillManifest.model_validate(sealed.manifest)
    assert approved.skill_metadata.status == "APPROVED"
    lifecycle = approved.governance.lifecycle
    assert lifecycle.state == "APPROVED"
    assert lifecycle.approval_required_for_change and lifecycle.immutable_after_approval
    certification = sealed.manifest["sbam_build_record"]["certification"]
    assert certification["state"] == "CERTIFIED" and certification["reused_from"] is None
    assert sealed.is_frozen
    assert sealed.sha256 == approved.governance.deterministic_hash
    assert SkillManifest.sealed(sealed.manifest).governance.deterministic_hash == sealed.sha256
    with build.sessions() as s:  # frozen means Postgres refuses the change, not just a flag
        with pytest.raises(DBAPIError, match="frozen") as refused:
            s.execute(
                text("UPDATE sbam.sub_skill_manifests SET is_frozen = false WHERE id = :i"),
                {"i": build.manifest_id},
            )
            s.commit()
        assert getattr(refused.value.orig, "sqlstate", None) == FROZEN_SQLSTATE
        s.rollback()

    moved = transition(build, "tests_passed")  # what SubSkillBuild applies after the stage
    assert (moved.from_state, moved.to_state) == ("TESTING", "CERTIFIED"), moved
    with build.sessions() as s:
        sub = s.get(SubSkill, build.inp.sub_skill_id)
        assert sub is not None and sub.state == "CERTIFIED"
        audit = s.execute(
            text(
                "SELECT action, correlation_id, after FROM sbam.audit_log"
                " WHERE id = :i AND entity_type = 'sub_skill'"
            ),
            {"i": moved.audit_id},
        ).one()
    assert audit.action == "tests_passed" and audit.correlation_id == build.inp.correlation_id
    assert audit.after["state"] == "CERTIFIED"
    print(
        "E14.1-T01",
        json.dumps(
            {
                "certification_id": str(cert_id),
                "content_sha256": cert.content_sha256,
                "sandbox_run_id": str(run_id),
                "mode": run.mode,
                "test_run_id": str(test_run.id),
                "manifest_status": approved.skill_metadata.status,
                "manifest_frozen": sealed.is_frozen,
                "sub_skill_state": "CERTIFIED",
                "audit_id": str(moved.audit_id),
            }
        ),
    )
