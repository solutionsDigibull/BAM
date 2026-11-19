"""Seeding for the E13.6 / E14.1 stack tests: one Sub-Skill build on the Compose Postgres, with the
evidence the judge and the certify stage read, written through the real models.

Every call makes its own tenant and project, so tests never see each other's rows; the scratch
database the rows live in is created at head and dropped by `scratch_db`. The shapes follow
`test_e13_2_activity_stack.seed` (proven on Postgres) and the unit `test_e14_1_certify.Build`
(the evidence a full certification needs); nothing here calls a model or a sandbox.
"""

import copy
import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fixtures import fixture_path
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.projects.models import Project
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sow.models import SowVersion
from sbam_api.sow.service import Generation, record_generation
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testruns.models import TestRunRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.canonical import sha256_of
from sbam_contracts.manifest import TEMPORAL_ROUTER, SkillManifest
from sbam_contracts.promptfoo import suite_sha256
from sbam_contracts.sow import SowDocument
from sbam_workers.certify.checklist import REQUIRED_GATES
from sbam_workers.test.promptfoo_suite import load_suite
from sqlalchemy import pool, text
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

DIGEST = "sha256:" + "ab" * 32  # the runtime the manifest names and the sandbox ran
BUNDLE = "b" * 64
NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
SOW_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
# name, kind, expected terminal: PASS_FIRST and ESCALATION are the kinds certification requires
CASES: tuple[tuple[str, str, str], ...] = (
    ("happy", "PASS_FIRST", "SUCCESS"),
    ("noop", "NO_OP", "SUCCESS"),
    ("stuck", "ESCALATION", "ESCALATED"),
)
ALL_GATES: tuple[GateName, ...] = (*REQUIRED_GATES, GateName.BANDIT)


@contextmanager
def scratch_db(prefix: str) -> Iterator[tuple[sessionmaker[Session], str]]:
    """A database of its own on the stack's Postgres, migrated to head and dropped afterwards.
    Yields its sessions and its name (a container on the stack reaches it at postgres:5432/<name>).
    """
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" OWNER sbam'))
    engine = make_engine(url(database=name), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=name))
            command.upgrade(config(), "head")
        yield make_sessionmaker(engine), name
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def structured() -> dict[str, Any]:
    """The ISA14 MS01.1 Sub-Skill (its ISATVON), as the build and the judge read it."""
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text("utf-8")
    )
    return raw


async def isatvon_of(_: BuildStageInput) -> dict[str, Any]:
    return structured()


@dataclass(frozen=True)
class Build:
    sessions: sessionmaker[Session]
    inp: BuildStageInput
    ctx: TenantContext
    manifest_id: uuid.UUID
    attempt_id: uuid.UUID
    dataset_id: uuid.UUID
    sandbox_run_id: uuid.UUID | None


def _approved_sow(s: Session, tenant: uuid.UUID) -> tuple[uuid.UUID, SowVersion]:
    project = Project(tenant_id=tenant, name=f"it-{uuid.uuid4().hex[:8]}", status="ACTIVE")
    project.state = "SOW_REVIEW"
    s.add(project)
    s.flush()
    kv = KnowledgeVersion(
        id=uuid7(),
        tenant_id=tenant,
        project_id=project.id,
        version=1,
        run_id=uuid7(),
        cognee_dataset="d",
        coverage={},
        state="READY",
    )
    s.add(kv)
    s.flush()
    doc = SowDocument.model_validate_json(SOW_FIXTURE.read_text("utf-8")).model_dump(mode="json")
    version = record_generation(
        s,
        TenantContext(tenant, project.id),
        Generation(uuid7(), kv.id, doc, None),
        correlation_id="cid-it",
    )
    # Seeding, as the unit test does: the approval flow itself is E7.1's (test_sow_approve_db).
    version.state = "APPROVED"
    version.approved_at = NOW
    version.approved_by = "fde@example.com"
    s.flush()
    return project.id, version


def draft_manifest(version: SowVersion) -> dict[str, Any]:
    """The MS01.1 manifest as `build_manifest` leaves it: DRAFT, built from this SOW version on
    the Temporal router, with no evidence or certification block yet."""
    data: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_manifest.json").read_text("utf-8")
    )
    data["skill_metadata"]["status"] = "DRAFT"
    data["governance"]["lifecycle"] = {
        "state": "DRAFT",
        "approval_required_for_change": True,
        "immutable_after_approval": False,
    }
    record = data["sbam_build_record"]
    record["provenance"]["sow_version_id"] = str(version.id)
    record["provenance"]["sow_sha256"] = version.sha256
    record["build"]["runtime_image_digest"] = DIGEST
    data["srim_runtime_definition"]["orchestration"]["router"] = TEMPORAL_ROUTER
    record.pop("evidence", None)
    record.pop("certification", None)
    return SkillManifest.sealed(copy.deepcopy(data)).dump()


def seed_build(
    sessions: sessionmaker[Session],
    *,
    gates: Sequence[GateName] = ALL_GATES,
    sandbox_mode: str | None = "CUBESANDBOX",
) -> Build:
    """A Sub-Skill whose build `attempt 0` is assembled and gated (`gates` PASSED, all on
    `BUNDLE`), with a three-case dataset and, unless `sandbox_mode` is None, a finished sandbox
    run of that dataset in that mode. No test run: `add_test_run` writes the judge's row."""
    tenant = uuid.uuid4()  # a tenant of its own per build
    isatvon = structured()
    workflow_id, macro_id, sub_id, attempt_id, manifest_id = (uuid7() for _ in range(5))
    with sessions() as s:
        project_id, version = _approved_sow(s, tenant)
        workflow = Workflow(
            id=workflow_id,
            tenant_id=tenant,
            project_id=project_id,
            sow_version_id=version.id,
            major=1,
            version="1.0.0",
            name="it",
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
                manifest=draft_manifest(version),
                sha256="sha256:" + "1" * 64,
                runtime_digest=DIGEST,
                created_by="SYSTEM",
            )
        )
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
                bundle_key="code/bundle.tar",
                files={"tool.py": "c" * 64},
                bundle_sha256=BUNDLE,
                created_by="SYSTEM",
            )
        )
        s.flush()
        for gate in gates:
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
                    bundle_sha256=BUNDLE,
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
            sha256="d" * 64,
            synthetic=True,
            edited_by_fde=False,
            generated_for="x",
            created_by="SYSTEM",
        )
        s.add(dataset)
        s.flush()
        for name, kind, terminal in CASES:
            s.add(
                CaseRow(
                    id=uuid7(),
                    tenant_id=tenant,
                    project_id=project_id,
                    dataset_id=dataset.id,
                    name=name,
                    kind=kind,
                    input={},
                    expected_terminal=terminal,
                    assertions=[],
                    created_by="SYSTEM",
                )
            )
        sandbox_run_id: uuid.UUID | None = None
        if sandbox_mode is not None:
            run = SandboxRunRecord(
                id=uuid7(),
                tenant_id=tenant,
                project_id=project_id,
                code_attempt_id=attempt_id,
                dataset_id=dataset.id,
                run_no=1,
                mode=sandbox_mode,
                status="FINISHED",
                exit_code=0,
                runtime_digest=DIGEST,
                truncated=False,
                is_frozen=True,
                temporal_workflow_id="build-it",
                activity_id="act-it",
                activity_attempt=1,
                started_at=NOW,
                finished_at=NOW,
                created_by="SYSTEM",
            )
            s.add(run)
            sandbox_run_id = run.id
        s.commit()
        dataset_id = dataset.id
    inp = BuildStageInput(
        tenant_id=tenant,
        project_id=project_id,
        run_id=uuid.uuid4(),
        correlation_id="cid-it",
        workflow_id=workflow_id,
        sub_skill_id=sub_id,
        build_no=1,
    )
    return Build(
        sessions,
        inp,
        TenantContext(tenant, project_id),
        manifest_id,
        attempt_id,
        dataset_id,
        sandbox_run_id,
    )


def current_suite(build: Build) -> str:
    """The suite hash the certify stage recomputes (same loader, same ISATVON)."""
    suite, _ = load_suite(build.sessions, build.inp, structured())
    return suite_sha256(suite)


def add_test_run(build: Build, *, state: str = "PASS", non_certifying: bool = False) -> uuid.UUID:
    """The judge's row for a green 3/3 run of the build's sandbox run."""
    assert build.sandbox_run_id is not None
    row = TestRunRow(
        tenant_id=build.ctx.tenant_id,
        project_id=build.ctx.project_id,
        sub_skill_id=build.inp.sub_skill_id,
        code_attempt_id=build.attempt_id,
        sandbox_run_id=build.sandbox_run_id,
        suite_sha256=current_suite(build),
        state=state,
        total=3,
        passed=3,
        failed=0,
        non_certifying=non_certifying,
        results=[
            {"case": name, "type": "terminal_state", "passed": True, "message": ""}
            for name, _, _ in CASES
        ],
        created_by="SYSTEM",
    )
    with build.sessions() as s:
        s.add(row)
        s.commit()
        return row.id
