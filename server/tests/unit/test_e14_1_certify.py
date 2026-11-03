"""E14.1: `certify_sub_skill` against a real (SQLite) database: the evidence it loads, the
certificate and APPROVED manifest it writes, and everything that must stop it."""

import asyncio
import copy
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testruns.models import TestRunRow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.canonical import deterministic_hash
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.promptfoo import suite_sha256
from sbam_workers.certify.checklist import REQUIRED_GATES
from sbam_workers.certify.subskill import BLOCKED, CertifySubSkill
from sbam_workers.test.promptfoo_suite import load_suite
from temporalio.exceptions import ApplicationError
from test_build_wiring import DIGEST, PROJECT, TENANT, World, structured

BUNDLE = "b" * 64
SOW_SHA = "4b313e3eba7ae02eafed5b8d877ff06f0a4ab889edcb12adea9142287f75b207"
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
CTX = TenantContext(TENANT, PROJECT)


async def isatvon_of(_: BuildStageInput) -> dict[str, Any]:
    return structured()


class Build(World):
    """A Sub-Skill whose build is fully evidenced; each test spoils one thing."""

    def __init__(self) -> None:
        super().__init__()
        bind = self.sessions.kw["bind"]
        for table in (
            GateResultRow,
            Sow,
            SowVersion,
            DatasetRow,
            CaseRow,
            SandboxRunRecord,
            TestRunRow,
            CertificationRow,
        ):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.sow_version = self.add_sow()
        self.manifest_id = self.add_manifest(0, self.draft())
        self.add_code_attempt(0, self.manifest_id)
        with self.sessions() as s:
            attempt = s.query(CodeAttempt).one()
            attempt.status = "ASSEMBLED"
            attempt.bundle_sha256 = BUNDLE
            self.attempt_id = attempt.id
            s.commit()
        self.dataset_id = self.add_dataset()
        self.suite = self.current_suite()
        self.gates = [*REQUIRED_GATES, GateName.BANDIT]
        for gate in self.gates:
            self.add_gate(gate)
        self.sandbox_id = self.add_sandbox()
        self.add_test_run(self.sandbox_id)

    # --- seeding ---------------------------------------------------------------------------

    def add_sow(self) -> uuid.UUID:
        with self.sessions() as s:
            sow = Sow(tenant_id=TENANT, project_id=PROJECT)
            s.add(sow)
            s.flush()
            version = SowVersion(
                tenant_id=TENANT,
                project_id=PROJECT,
                sow_id=sow.id,
                version=1,
                source="GENERATED",
                run_id=uuid7(),
                knowledge_version_id=uuid7(),
                structured={},
                sha256=SOW_SHA,
            )
            version.state = "APPROVED"
            version.approved_at = NOW
            s.add(version)
            s.commit()
            return version.id

    def draft(self) -> dict[str, Any]:
        data = copy.deepcopy(self.manifest_data())
        data["skill_metadata"]["status"] = "DRAFT"
        data["governance"]["lifecycle"] = {
            "state": "DRAFT",
            "approval_required_for_change": True,
            "immutable_after_approval": False,
        }
        data["sbam_build_record"]["provenance"]["sow_version_id"] = str(self.sow_version)
        data["sbam_build_record"].pop("evidence", None)
        data["sbam_build_record"].pop("certification", None)
        return SkillManifest.sealed(data).dump()

    def add_dataset(self) -> uuid.UUID:
        with self.sessions() as s:
            row = DatasetRow(
                tenant_id=TENANT,
                project_id=PROJECT,
                sub_skill_id=self.sub,
                version=1,
                sha256="d" * 64,
            )
            s.add(row)
            s.flush()
            for name, kind, terminal in (
                ("happy", "PASS_FIRST", "SUCCESS"),
                ("noop", "NO_OP", "SUCCESS"),
                ("stuck", "ESCALATION", "ESCALATED"),
            ):
                s.add(
                    CaseRow(
                        tenant_id=TENANT,
                        project_id=PROJECT,
                        dataset_id=row.id,
                        name=name,
                        kind=kind,
                        input={},
                        expected_terminal=terminal,
                        assertions=[],
                    )
                )
            s.commit()
            return row.id

    def current_suite(self) -> str:
        suite, _ = load_suite(self.sessions, self.stage(), structured())
        return suite_sha256(suite)

    def add_gate(self, gate: GateName, status: str = "PASSED", findings: Any = None) -> None:
        with self.sessions() as s:
            s.add(
                GateResultRow(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    sub_skill_id=self.sub,
                    code_attempt_id=self.attempt_id,
                    gate=gate.value,
                    status=status,
                    findings=findings or [],
                    build_no=1,
                    cycle=0,
                    attempt=0,
                    bundle_sha256=BUNDLE,
                )
            )
            s.commit()

    def add_sandbox(self, mode: str = "CUBESANDBOX") -> uuid.UUID:
        with self.sessions() as s:
            row = SandboxRunRecord(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=PROJECT,
                code_attempt_id=self.attempt_id,
                dataset_id=self.dataset_id,
                run_no=1,
                mode=mode,
                status="FINISHED",
                runtime_digest=DIGEST,
                truncated=False,
                is_frozen=True,
                temporal_workflow_id="wf",
                activity_id="act",
                activity_attempt=1,
                started_at=NOW,
                finished_at=NOW,
                created_by="test",
            )
            s.add(row)
            s.commit()
            return row.id

    def add_test_run(self, sandbox_id: uuid.UUID, **over: Any) -> None:
        values: dict[str, Any] = {
            "tenant_id": TENANT,
            "project_id": PROJECT,
            "sub_skill_id": self.sub,
            "code_attempt_id": self.attempt_id,
            "sandbox_run_id": sandbox_id,
            "suite_sha256": self.suite,
            "state": "PASS",
            "total": 3,
            "passed": 3,
            "failed": 0,
            "non_certifying": False,
            "results": [
                {"case": n, "type": "terminal_state", "passed": True, "message": ""}
                for n in ("happy", "noop", "stuck")
            ],
            "created_by": "test",
        }
        with self.sessions() as s:
            s.add(TestRunRow(**{**values, **over}))
            s.commit()

    # --- running ---------------------------------------------------------------------------

    def certify(self) -> uuid.UUID:
        stage = CertifySubSkill(self.sessions, isatvon_of, clock=lambda: NOW)
        return asyncio.run(stage.run(self.stage())).id

    def blocked_with(self) -> str:
        with pytest.raises(ApplicationError) as err:
            self.certify()
        assert err.value.type == BLOCKED and err.value.non_retryable
        return str(err.value)

    def certificates(self) -> list[CertificationRow]:
        with self.sessions() as s:
            return list(s.query(CertificationRow))

    def manifest_row(self) -> SubSkillManifest:
        with self.sessions() as s:
            return s.get(SubSkillManifest, self.manifest_id)  # type: ignore[return-value]


@pytest.fixture
def b() -> Build:
    return Build()


def test_e14_1_t01_full_evidence_certifies_and_approves_the_manifest(b: Build) -> None:
    cert_id = b.certify()
    (cert,) = b.certificates()
    assert cert.id == cert_id and cert.level == "SUB_SKILL" and cert.is_frozen
    assert cert.content_sha256 == "sha256:" + BUNDLE
    assert cert.evidence["test_run_id"] and cert.evidence["suite_sha256"] == b.suite
    row = b.manifest_row()
    manifest = SkillManifest.model_validate(row.manifest)
    assert manifest.skill_metadata.status == "APPROVED"
    assert manifest.governance.lifecycle.immutable_after_approval
    assert manifest.sbam_build_record.certification is not None
    assert manifest.sbam_build_record.certification.reused_from is None
    assert row.is_frozen


def test_e14_1_t09_the_approved_manifest_hash_recomputes(b: Build) -> None:
    b.certify()
    row = b.manifest_row()
    assert row.sha256 == row.manifest["governance"]["deterministic_hash"]
    assert deterministic_hash(row.manifest) == row.sha256
    stripped = copy.deepcopy(row.manifest)
    stripped["sbam_build_record"]["evidence"] = None
    stripped["sbam_build_record"].pop("certification")
    assert deterministic_hash(stripped) == row.sha256  # evidence and certification are outside it


def test_e14_1_t07_a_retried_activity_finds_its_certificate(b: Build) -> None:
    first = b.certify()
    before = b.manifest_row().manifest
    assert b.certify() == first
    assert len(b.certificates()) == 1
    assert b.manifest_row().manifest == before


def test_e14_1_t02_a_missing_gate_blocks_and_writes_nothing() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(GateResultRow).filter_by(gate="MYPY").delete()
        s.commit()
    assert "MISSING_EVIDENCE:MYPY" in b.blocked_with()
    assert b.certificates() == [] and not b.manifest_row().is_frozen
    assert b.manifest_row().manifest["skill_metadata"]["status"] == "DRAFT"


def test_e14_1_t03_a_local_docker_run_cannot_certify() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(SandboxRunRecord).one().mode = "LOCAL_DOCKER_NONCERT"
        s.commit()
    assert "NON_CERTIFYING_RUN" in b.blocked_with()
    assert b.certificates() == []


def test_e14_1_a_dataset_edited_after_the_run_blocks() -> None:
    b = Build()
    with b.sessions() as s:  # a new version is the FDE's edit; the run judged version 1
        s.add(
            DatasetRow(
                tenant_id=TENANT, project_id=PROJECT, sub_skill_id=b.sub, version=2, sha256="f" * 64
            )
        )
        s.commit()
    assert "DATASET_CHANGED" in b.blocked_with()


def test_e14_1_an_unapproved_or_changed_sow_blocks() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(SowVersion).one().state = "SUPERSEDED"
        s.commit()
    assert "SOW_NOT_APPROVED" in b.blocked_with()
    c = Build()
    with c.sessions() as s:
        s.query(SowVersion).one().sha256 = "0" * 64
        s.commit()
    assert "SOW_HASH_MISMATCH" in c.blocked_with()


def test_e14_1_gates_on_a_different_bundle_block() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(GateResultRow).filter_by(gate="RUFF").one().bundle_sha256 = "c" * 64
        s.commit()
    assert "EVIDENCE_HASH_MISMATCH" in b.blocked_with()


def test_e14_1_a_changed_runtime_digest_blocks() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(SandboxRunRecord).one().runtime_digest = "sha256:" + "9" * 64
        s.commit()
    assert "RUNTIME_CHANGED" in b.blocked_with()


def test_e14_1_the_certificate_is_tenant_scoped(b: Build) -> None:
    b.certify()
    with b.sessions() as s:
        other = TenantContext(uuid.uuid4(), PROJECT)
        assert (
            CertificationRepository(s, other).find("SUB_SKILL", b.sub, "sha256:" + BUNDLE) is None
        )
        assert SubSkillManifestRepository(s, CTX).get(b.manifest_id) is not None
