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
from sbam_contracts.manifest import TEMPORAL_ROUTER, SkillManifest
from sbam_contracts.promptfoo import suite_sha256
from sbam_contracts.testgen import CaseKind
from sbam_workers.certify.checklist import REQUIRED_GATES
from sbam_workers.certify.subskill import BLOCKED, CertifySubSkill, _passed_kinds
from sbam_workers.test.promptfoo_suite import load_suite
from temporalio.exceptions import ApplicationError
from test_build_wiring import DIGEST, PROJECT, TENANT, WORKFLOW, World, structured

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
        # a manifest SBAM builds now names the Temporal router (ADR 0031)
        data["srim_runtime_definition"]["orchestration"]["router"] = TEMPORAL_ROUTER
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

    def add_gate(
        self,
        gate: GateName,
        status: str = "PASSED",
        findings: Any = None,
        *,
        attempt_id: uuid.UUID | None = None,
        build_no: int = 1,
    ) -> None:
        with self.sessions() as s:
            s.add(
                GateResultRow(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    sub_skill_id=self.sub,
                    code_attempt_id=attempt_id or self.attempt_id,
                    gate=gate.value,
                    status=status,
                    findings=findings or [],
                    build_no=build_no,
                    cycle=0,
                    attempt=0,
                    bundle_sha256=BUNDLE,
                )
            )
            s.commit()

    def add_sandbox(
        self, mode: str = "CUBESANDBOX", *, attempt_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        with self.sessions() as s:
            row = SandboxRunRecord(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=PROJECT,
                code_attempt_id=attempt_id or self.attempt_id,
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

    def add_build(self, build_no: int) -> tuple[uuid.UUID, uuid.UUID]:
        """A later build of the Sub-Skill whose bundle is byte-identical to build 1's, with a
        draft manifest and no evidence yet: (manifest id, code attempt id)."""
        with self.sessions() as s:
            manifest = SubSkillManifest(
                tenant_id=TENANT,
                project_id=PROJECT,
                sub_skill_id=self.sub,
                build_no=build_no,
                cycle=0,
                attempt=0,
                manifest=self.draft(),
                sha256="sha256:" + "2" * 64,
                runtime_digest=DIGEST,
            )
            s.add(manifest)
            s.flush()
            attempt = CodeAttempt(
                tenant_id=TENANT,
                project_id=PROJECT,
                workflow_id=WORKFLOW,
                sub_skill_id=self.sub,
                manifest_id=manifest.id,
                build_no=build_no,
                cycle=0,
                attempt=0,
                template_name=self.template.name,
                template_version=self.template.version,
                template_sha256=self.template.sha256,
                status="BODIES_GENERATED",
                bodies={},
                llm_call_ids=[],
                trace_id=f"t{build_no}",
                findings=[],
                created_by="sbam-worker",
            )
            s.add(attempt)
            s.commit()
            attempt.status = "ASSEMBLED"
            attempt.bundle_sha256 = BUNDLE  # byte-identical to build 1's bundle
            s.commit()
            return manifest.id, attempt.id

    def build_stage(self, build_no: int) -> BuildStageInput:
        return self.stage().model_copy(update={"build_no": build_no})

    # --- running ---------------------------------------------------------------------------

    def certify(self, inp: BuildStageInput | None = None) -> uuid.UUID:
        stage = CertifySubSkill(self.sessions, isatvon_of, clock=lambda: NOW)
        return asyncio.run(stage.run(inp or self.stage())).id

    def blocked_with(self, inp: BuildStageInput | None = None) -> str:
        with pytest.raises(ApplicationError) as err:
            self.certify(inp)
        assert err.value.type == BLOCKED and err.value.non_retryable
        return str(err.value)

    def certificates(self) -> list[CertificationRow]:
        with self.sessions() as s:
            return list(s.query(CertificationRow))

    def manifest_row(self, manifest_id: uuid.UUID | None = None) -> SubSkillManifest:
        with self.sessions() as s:
            row = s.get(SubSkillManifest, manifest_id or self.manifest_id)
            assert row is not None
            return row


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


def test_e14_1_a_new_build_with_the_same_bundle_is_rechecked_not_handed_the_old_certificate(
    b: Build,
) -> None:
    """Build 2 assembled a byte-identical bundle, but its SOW is no longer APPROVED: build 1's
    certificate is not build 2's, so the checklist runs and blocks it."""
    first = b.certify()
    with b.sessions() as s:
        s.query(SowVersion).one().state = "SUPERSEDED"
        s.commit()
    manifest2, _ = b.add_build(2)
    assert "SOW_NOT_APPROVED" in b.blocked_with(b.build_stage(2))
    row2 = b.manifest_row(manifest2)
    assert row2.manifest["skill_metadata"]["status"] == "DRAFT" and not row2.is_frozen
    certs = b.certificates()
    assert len(certs) == 1 and certs[0].id == first


def test_e14_1_a_new_build_with_the_same_bundle_and_good_evidence_seals_its_own_manifest(
    b: Build,
) -> None:
    """Build 2 has complete evidence of its own on the same bundle: it certifies, its manifest
    is sealed APPROVED and frozen, and the one certificate per bundle is the one on file."""
    first = b.certify()
    before = b.certificates()[0].evidence
    manifest2, attempt2 = b.add_build(2)
    for gate in b.gates:
        b.add_gate(gate, attempt_id=attempt2, build_no=2)
    b.add_test_run(b.add_sandbox(attempt_id=attempt2), code_attempt_id=attempt2)
    assert b.certify(b.build_stage(2)) == first
    row2 = b.manifest_row(manifest2)
    assert row2.manifest["skill_metadata"]["status"] == "APPROVED" and row2.is_frozen
    assert row2.manifest["sbam_build_record"]["evidence"]["code_attempt_id"] == str(attempt2)
    (cert,) = b.certificates()
    assert cert.id == first and cert.evidence == before  # the frozen certificate is untouched
    assert b.certify(b.build_stage(2)) == first  # a retry of build 2 finds it and writes nothing
    assert b.manifest_row(manifest2).manifest == row2.manifest and len(b.certificates()) == 1


def test_e14_1_t05_a_case_with_no_result_is_not_a_passed_scenario() -> None:
    """A dataset case with no row in the run's results did not pass: it was never tested."""
    kinds = {"happy": CaseKind.PASS_FIRST, "noop": CaseKind.NO_OP, "stuck": CaseKind.ESCALATION}
    results: list[dict[str, Any]] = [
        {"case": "happy", "passed": True},
        {"case": "noop", "passed": True},
    ]
    assert _passed_kinds(results, kinds) == frozenset({CaseKind.PASS_FIRST, CaseKind.NO_OP})


def test_e14_1_t02_a_missing_gate_blocks_and_writes_nothing() -> None:
    b = Build()
    with b.sessions() as s:
        s.query(GateResultRow).filter_by(gate="MYPY").delete()
        s.commit()
    assert "MISSING_EVIDENCE:MYPY" in b.blocked_with()
    assert b.certificates() == [] and not b.manifest_row().is_frozen
    assert b.manifest_row().manifest["skill_metadata"]["status"] == "DRAFT"


def test_adr0031_a_new_sbam_built_manifest_with_another_router_is_refused() -> None:
    b = Build()
    with b.sessions() as s:
        row = s.get(SubSkillManifest, b.manifest_id)
        assert row is not None
        data = copy.deepcopy(row.manifest)
        data["srim_runtime_definition"]["orchestration"]["router"] = "Dify_Dynamic_Master_Router"
        row.manifest = SkillManifest.sealed(data).dump()
        s.commit()
    assert "ROUTER_NOT_TEMPORAL" in b.blocked_with()
    assert b.certificates() == [] and not b.manifest_row().is_frozen


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
