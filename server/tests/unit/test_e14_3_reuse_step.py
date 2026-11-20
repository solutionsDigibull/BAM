"""E14.3: `reuse_certifications` against a real (SQLite) database: an unchanged Sub-Skill carried
into a new Workflow version reuses its certificate (E14.3-T01, T06, T07), and everything that must
stop it does (T02 to T05, a local-docker run, another tenant or project).

The world is the E9.1 one (a real approved SOW, knowledge version, master snapshot, runtime registry
and Sub-Skills from ISATVON pass 2), so `build_manifest`'s own loader computes the copy's current
manifest. The source Workflow's Sub-Skills are certified by hand with the rows `certify_sub_skill`
writes; the new Workflow's copies are what `carry_over_workflow` leaves (CERTIFIED,
`reused_from_id`).
Storage is an in-memory fake: nothing here needs Docker, a CubeSandbox host or a model.
"""

import copy
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from knowledge_db import TENANT, World
from llm_db import FakeBifrost, completion
from registry_db import sample_registry
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.masters.models import MasterSnapshot
from sbam_api.registry.models import RuntimeRegistryPackageRow, RuntimeRegistryRow
from sbam_api.registry.repository import upsert_registry
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.srim_defaults.models import TenantSrimDefaults
from sbam_api.state_machine import SYSTEM_ACTOR_ID
from sbam_api.storage.base import StorageBackend
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testruns.models import TestRunRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.build import BuildStageInput
from sbam_contracts.canonical import deterministic_hash
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.promptfoo import suite_sha256
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.build import manifest as build_manifest_module
from sbam_workers.build.codegen.bundle import make_bundle
from sbam_workers.build.manifest import SqlManifestStore, build_for
from sbam_workers.certify import reuse, reuse_step, stubs
from sbam_workers.certify.reuse_step import (
    REFUSED_ACTION,
    REUSED_ACTION,
    STAGE,
    TAMPER_ACTION,
    ReuseCertifications,
    sql_current_manifest,
)
from sbam_workers.certify.subskill import approved_manifest
from sbam_workers.test.suite import build_suite
from sqlalchemy import select, text, update
from sub_skills_db import Pass2, answer, proposal

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
NAMES = ("Investor Auth Manager", "Portfolio Viewer", "Report Builder", "Alert Sender", "Exporter")


class FakeStorage:
    """The part of `StorageBackend` the reuse step reads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, key: str, *, verify: bool = False) -> bytes:
        return self.objects[key]


class Reuse:
    """Workflow 1.0.0 with certified Sub-Skills, and a new Workflow carrying copies of them."""

    def __init__(
        self, tmp: Path, names: tuple[str, ...] = NAMES[:3], version: str = "1.0.0"
    ) -> None:
        self.p = Pass2(World(tmp))
        self.sessions = self.p.w.sessions
        engine = self.sessions.kw["bind"]
        for table in (
            CodeAttempt,
            SubSkillManifest,
            GateResultRow,
            WorkflowSchema,
            MasterSnapshot,
            RuntimeRegistryRow,
            RuntimeRegistryPackageRow,
            AuditLog,
            TenantSrimDefaults,
            CertificationRow,
            SandboxRunRecord,
            TestRunRow,
            DatasetRow,
            CaseRow,
        ):
            table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        skills = [proposal(n, "F-ACCESS-1", "F-ACCESS-2") for n in names]
        self.subs = self.p.run_macro(FakeBifrost([completion(answer(*skills))]))
        with self.sessions() as s:
            s.execute(
                update(SowVersion)
                .where(SowVersion.id == self.p.version)
                .values(approved_at=NOW, approved_by="fde-1")
            )
            snapshot = MasterSnapshot(
                tenant_id=TENANT,
                project_id=self.p.w.project,
                entries=[],
                sha256="sha256:" + "ef" * 32,
            )
            s.add(snapshot)
            s.flush()
            s.execute(
                update(KnowledgeVersion)
                .where(KnowledgeVersion.id == self.p.kv)
                .values(okf_sha256="cd" * 32, master_snapshot_id=snapshot.id)
            )
            upsert_registry(s, sample_registry("34"), activate=True)
            s.commit()
        self.storage = FakeStorage()
        self.store = SqlManifestStore(self.sessions)
        self.ctx = TenantContext(TENANT, self.p.w.project)
        self.certs: dict[uuid.UUID, uuid.UUID] = {}  # source Sub-Skill -> its certificate
        self.attempts: dict[uuid.UUID, uuid.UUID] = {}
        self.sandboxes: dict[uuid.UUID, uuid.UUID] = {}
        for sub in self.subs:
            self.certify_source(sub, version)
        self.version2 = self.second_sow_version()
        self.workflow2 = self.p.add_workflow(self.version2, major=2)
        self.copies: dict[uuid.UUID, uuid.UUID] = {}  # source Sub-Skill -> its copy

    def second_sow_version(self) -> uuid.UUID:
        """The scope change's SOW version 2, approved like version 1 (a Workflow is one per SOW
        version)."""
        with self.sessions() as s:
            v1 = s.get(SowVersion, self.p.version)
            assert v1 is not None
            v2 = SowVersion(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.p.w.project,
                sow_id=v1.sow_id,
                version=2,
                source=v1.source,
                run_id=uuid7(),
                knowledge_version_id=v1.knowledge_version_id,
                structured=v1.structured,
                sha256=v1.sha256,
            )
            v2.state = "APPROVED"
            v2.approved_at = NOW
            v2.approved_by = "fde-1"
            s.add(v2)
            s.flush()
            sow = s.get(Sow, v1.sow_id)
            assert sow is not None
            sow.current_version_id = sow.approved_version_id = v2.id  # where the approval left it
            s.commit()
            return v2.id

    # --- the source Workflow, certified ------------------------------------------------------

    def stage(self, sub: uuid.UUID, workflow: uuid.UUID | None = None) -> BuildStageInput:
        return BuildStageInput.model_validate(
            {
                **self.p.inp.model_dump(),
                "workflow_id": workflow or self.p.workflow,
                "sub_skill_id": sub,
                "build_no": 1,
            }
        )

    def certify_source(self, sub: uuid.UUID, version: str = "1.0.0") -> None:
        manifest_id = build_for(self.store, self.stage(sub)).id
        files = {"action.py": f"# {sub}".encode(), "loop_spec.json": b"{}"}
        bundle = make_bundle(files)
        key = f"code/{sub}"
        self.storage.objects[key] = bundle.tar
        with self.sessions() as s:
            row = s.get(SubSkillManifest, manifest_id)
            sub_row = s.get(SubSkill, sub)
            assert row is not None and sub_row is not None
            draft = copy.deepcopy(row.manifest)
            draft["skill_metadata"]["version"] = version
            draft["governance"]["versioning"]["patch"] = int(version.rsplit(".", 1)[1])
            dataset = DatasetRow(
                tenant_id=TENANT,
                project_id=self.p.w.project,
                sub_skill_id=sub,
                version=1,
                sha256="d" * 64,
            )
            s.add(dataset)
            s.flush()
            cases = [
                ("happy", "PASS_FIRST", "SUCCESS"),
                ("noop", "NO_OP", "SUCCESS"),
                ("stuck", "ESCALATION", "ESCALATED"),
            ]
            for name, kind, terminal in cases:
                s.add(
                    CaseRow(
                        tenant_id=TENANT,
                        project_id=self.p.w.project,
                        dataset_id=dataset.id,
                        name=name,
                        kind=kind,
                        input={},
                        expected_terminal=terminal,
                        assertions=[],
                    )
                )
            suite = suite_sha256(build_suite(dict(sub_row.isatvon), self.cases(name_of=cases)))
            attempt = CodeAttempt(
                tenant_id=TENANT,
                project_id=self.p.w.project,
                workflow_id=self.p.workflow,
                sub_skill_id=sub,
                manifest_id=manifest_id,
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="fastmcp-loopy",
                template_version="1.0.1",
                template_sha256="a" * 64,
                status="ASSEMBLED",
                bodies={},
                llm_call_ids=[],
                trace_id="t",
                findings=[],
                bundle_key=key,
                files=dict(bundle.hashes),
                bundle_sha256=bundle.bundle_sha256,
                created_by="sbam-worker",
            )
            s.add(attempt)
            s.flush()
            sandbox = SandboxRunRecord(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.p.w.project,
                code_attempt_id=attempt.id,
                dataset_id=dataset.id,
                run_no=1,
                mode="CUBESANDBOX",
                status="FINISHED",
                runtime_digest="sha256:" + "34" * 32,
                truncated=False,
                is_frozen=True,
                temporal_workflow_id="wf",
                activity_id="act",
                activity_attempt=1,
                started_at=NOW,
                finished_at=NOW,
                created_by="test",
            )
            s.add(sandbox)
            s.flush()
            run = TestRunRow(
                tenant_id=TENANT,
                project_id=self.p.w.project,
                sub_skill_id=sub,
                code_attempt_id=attempt.id,
                sandbox_run_id=sandbox.id,
                suite_sha256=suite,
                state="PASS",
                total=3,
                passed=3,
                failed=0,
                non_certifying=False,
                results=[],
                created_by="test",
            )
            s.add(run)
            s.flush()
            record = {
                "code_attempt_id": str(attempt.id),
                "bundle_sha256": bundle.bundle_sha256,
                "gates": [{"gate": "AST", "status": "PASSED"}],
                "test_run_id": str(run.id),
                "sandbox_run_id": str(sandbox.id),
                "passed_cases": 3,
            }
            approved = approved_manifest(draft, record, NOW)
            row.manifest = approved.dump()
            row.sha256 = approved.governance.deterministic_hash
            row.is_frozen = True
            cert = CertificationRepository(s, self.ctx).record(
                {
                    "level": "SUB_SKILL",
                    "subject_id": sub,
                    "content_sha256": "sha256:" + bundle.bundle_sha256,
                    "evidence": {
                        **record,
                        "dataset_sha256": "d" * 64,
                        "suite_sha256": suite,
                        "manifest_sha256": row.sha256,
                    },
                    "certified_at": NOW,
                    "created_by": "test",
                }
            )
            sub_row.state = "CERTIFIED"
            s.commit()
            self.certs[sub] = cert.id
            self.attempts[sub] = attempt.id
            self.sandboxes[sub] = sandbox.id

    @staticmethod
    def cases(name_of: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "kind": k,
                "input": {},
                "expected_terminal": t,
                "expected_output": None,
                "assertions": [],
            }
            for n, k, t in name_of
        ]

    # --- the new Workflow, as carry-over leaves it ---------------------------------------------

    def carry(self, sub: uuid.UUID, *, state: str = "CERTIFIED", reused: bool = True) -> uuid.UUID:
        with self.sessions() as s:
            source = s.get(SubSkill, sub)
            assert source is not None
            old_macro = s.get(MacroSkill, source.macro_skill_id)
            assert old_macro is not None
            macros = s.scalars(
                select(MacroSkill).where(MacroSkill.workflow_id == self.workflow2)
            ).all()
            macro = macros[0] if macros else None
            if macro is None:
                macro = MacroSkill(
                    id=uuid7(),
                    tenant_id=TENANT,
                    project_id=self.p.w.project,
                    workflow_id=self.workflow2,
                    sow_version_id=self.p.version,
                    macro_category=old_macro.macro_category,
                    name=old_macro.name,
                    description=old_macro.description,
                    order=old_macro.order,
                    sow_refs=list(old_macro.sow_refs),
                    agent_ref=None,
                    validation_errors=[],
                    created_by="SYSTEM",
                )
                macro.state = "CERTIFIED"
                s.add(macro)
            row = SubSkill(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.p.w.project,
                macro_skill_id=macro.id,
                workflow_id=self.workflow2,
                skill_id=source.skill_id,
                skill_name=source.skill_name,
                order=source.order,
                isatvon=copy.deepcopy(dict(source.isatvon)),
                isatvon_framework_version=source.isatvon_framework_version,
                sow_refs=list(source.sow_refs),
                validation_errors=[],
                content_sha256=source.content_sha256,
                reused_from_id=source.id if reused else None,
                created_by="SYSTEM",
            )
            row.state = state
            s.add(row)
            s.commit()
            self.copies[sub] = row.id
            return row.id

    def ref(self, **over: Any) -> WorkflowRef:
        values = {**self.p.inp.model_dump(), "workflow_id": self.workflow2, **over}
        return WorkflowRef.model_validate(values)

    def step(self) -> ReuseCertifications:
        return ReuseCertifications(
            self.sessions,
            cast(StorageBackend, self.storage),
            sql_current_manifest(self.sessions),
            clock=lambda: NOW,
        )

    def run(self, ref: WorkflowRef | None = None) -> list[uuid.UUID]:
        return self.step().reuse_all(ref or self.ref())

    # --- reading back --------------------------------------------------------------------------

    def certificate_of(self, copy_id: uuid.UUID) -> CertificationRow | None:
        with self.sessions() as s:
            row = s.scalars(
                select(CertificationRow).where(CertificationRow.subject_id == copy_id)
            ).first()
            s.expunge_all()
            return row

    def sub(self, sub_id: uuid.UUID) -> SubSkill:
        with self.sessions() as s:
            row = s.get(SubSkill, sub_id)
            assert row is not None
            s.expunge_all()
            return row

    def audits(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            rows = list(s.scalars(select(AuditLog).where(AuditLog.action == action)))
            s.expunge_all()
            return rows

    def manifest_of(self, sub_id: uuid.UUID) -> SubSkillManifest:
        with self.sessions() as s:
            row = s.scalars(
                select(SubSkillManifest).where(SubSkillManifest.sub_skill_id == sub_id)
            ).one()
            s.expunge_all()
            return row

    def count(self, model: type) -> int:
        with self.sessions() as s:
            return len(list(s.scalars(select(model))))


@pytest.fixture
def w(tmp_path: Path) -> Iterator[Reuse]:
    yield Reuse(tmp_path)


def thaw(session: Any) -> None:
    """Test-only surgery on certificates the freeze trigger would refuse to change."""
    for op in ("update", "delete"):
        session.execute(text(f'DROP TRIGGER IF EXISTS "certifications_frozen_{op}"'))


def refused_with(w: Reuse, source: uuid.UUID) -> dict[str, Any]:
    """The one refusal row of this Sub-Skill's copy, and the copy is back to VALID, not reused."""
    copy_id = w.copies[source]
    [row] = [a for a in w.audits(REFUSED_ACTION) if a.entity_id == str(copy_id)]
    assert w.certificate_of(copy_id) is None
    after = w.sub(copy_id)
    assert after.state == "VALID" and after.reused_from_id is None
    return row.after or {}


# --- T01 / T07: an unchanged Sub-Skill reuses, the changed one is rebuilt ---------------------


def test_e14_3_t01_patch_reuses_the_others_and_rebuilds_only_the_patched(tmp_path: Path) -> None:
    w = Reuse(tmp_path, NAMES)  # five Sub-Skills certified in 1.0.0
    for sub in w.subs:
        w.carry(sub)
    patched = w.subs[1]  # the FDE patches MS01.2: its content differs in the new version
    with w.sessions() as s:
        s.execute(
            update(SubSkill)
            .where(SubSkill.id == w.copies[patched])
            .values(skill_name="Portfolio Viewer 2")
        )
        row = s.get(SubSkill, w.copies[patched])
        assert row is not None
        isatvon = copy.deepcopy(dict(row.isatvon))
        isatvon["skill_metadata"]["skill_name"] = "Portfolio Viewer 2"
        row.isatvon = isatvon
        s.commit()
    reused = w.run()
    assert len(reused) == 4
    for source in w.subs:
        copied = w.copies[source]
        if source == patched:
            assert "skill_name" in refused_with(w, source)["differing"]
            continue
        cert = w.certificate_of(copied)
        assert cert is not None and cert.id in reused
        assert cert.reused_from_id == w.certs[source]  # reused_from_id resolves to the source
        source_cert = w.certificate_of(source)
        assert source_cert is not None and cert.content_sha256 == source_cert.content_sha256
        assert w.sub(copied).state == "CERTIFIED"  # no second certification ran


def test_e14_3_t07_scope_change_major_reuses_the_untouched_and_leaves_the_affected(
    tmp_path: Path,
) -> None:
    w = Reuse(tmp_path, NAMES)
    affected = w.subs[2]
    for sub in w.subs:  # what carry_over_workflow leaves: the affected one is DRAFT, not reused
        if sub == affected:
            w.carry(sub, state="DRAFT", reused=False)
        else:
            w.carry(sub)
    assert len(w.run()) == 4
    assert w.certificate_of(w.copies[affected]) is None
    assert w.sub(w.copies[affected]).state == "DRAFT"  # untouched: extraction and build take it
    assert w.audits(REFUSED_ACTION) == []
    with w.sessions() as s:
        workflow = s.get(Workflow, w.workflow2)
        assert workflow is not None and workflow.version == "2.0.0"


# --- what the reused rows say -----------------------------------------------------------------


def test_e14_3_t06_a_reused_sub_skill_keeps_its_version_and_names_its_source(
    tmp_path: Path,
) -> None:
    w = Reuse(tmp_path, NAMES[:1], version="1.0.2")  # certified at 1.0.2 in Workflow 1.0.2
    [source] = w.subs
    copied = w.carry(source)
    [cert_id] = w.run()
    row = w.manifest_of(copied)
    manifest = SkillManifest.model_validate(row.manifest)
    assert manifest.skill_metadata.version == "1.0.2"  # not rebuilt at 1.0.0
    assert manifest.governance.versioning.patch == 2
    record = manifest.sbam_build_record
    assert record.certification is not None
    assert record.certification.reused_from == str(w.certs[source])
    assert record.ids.workflow_id == str(w.workflow2)  # the new Workflow's own ids and provenance
    assert row.is_frozen and manifest.skill_metadata.status == "APPROVED"
    assert deterministic_hash(row.manifest) == row.sha256
    cert = w.certificate_of(copied)
    assert cert is not None and cert.id == cert_id
    assert cert.evidence["manifest_sha256"] == row.sha256
    assert cert.evidence["source_code_attempt_id"] == str(w.attempts[source])
    # its code attempt points at the same stored bundle, so the Workflow checks can read it
    with w.sessions() as s:
        [attempt] = s.scalars(select(CodeAttempt).where(CodeAttempt.sub_skill_id == copied))
        old = s.get(CodeAttempt, w.attempts[source])
        assert old is not None and attempt.bundle_key == old.bundle_key
        assert attempt.bundle_sha256 == old.bundle_sha256 and attempt.workflow_id == w.workflow2


def test_e14_3_the_source_manifest_and_certificate_are_left_as_they_were(w: Reuse) -> None:
    [first, *_] = w.subs
    before = w.manifest_of(first)
    w.carry(first)
    w.run()
    after = w.manifest_of(first)
    assert (after.sha256, after.manifest, after.is_frozen) == (
        before.sha256,
        before.manifest,
        before.is_frozen,
    )
    assert after.manifest["sbam_build_record"]["certification"]["reused_from"] is None


def test_e14_3_the_audit_row_carries_the_compared_hashes_and_nothing_else(w: Reuse) -> None:
    [first, *_] = w.subs
    copied = w.carry(first)
    w.run()
    [row] = w.audits(REUSED_ACTION)
    after = row.after or {}
    assert row.entity_id == str(copied) and row.actor_id == SYSTEM_ACTOR_ID
    assert after["reuse"] is True and after["differing"] == [] and after["evidence_verified"]
    assert after["prior"] == after["current"] and set(after["prior"]) == set(reuse.PARTS)
    assert after["prior_key"] == after["current_key"]
    assert after["source_certification_id"] == str(w.certs[first])
    flat = str(after)
    assert "def " not in flat and "action.py" not in flat  # ids and hashes only, never code


def test_e14_3_a_retried_activity_writes_nothing_twice(w: Reuse) -> None:
    for sub in w.subs:
        w.carry(sub)
    first = w.run()

    def counts() -> tuple[int, ...]:
        return tuple(
            w.count(m) for m in (CertificationRow, SubSkillManifest, CodeAttempt, AuditLog)
        )

    before = counts()
    assert sorted(w.run()) == sorted(first)
    assert counts() == before


# --- T02 / T03 / T04: any difference in any part forces a rebuild ------------------------------


def test_e14_3_t02_a_new_runtime_digest_reuses_nothing(w: Reuse) -> None:
    for sub in w.subs:
        w.carry(sub)
    with w.sessions() as s:  # the platform bumps the runtime image
        upsert_registry(s, sample_registry("35"), activate=True)
        s.commit()
    assert w.run() == []
    for sub in w.subs:
        assert refused_with(w, sub)["differing"] == ["runtime_image_digest"]


def test_e14_3_t03_a_case_the_fde_added_reuses_nothing(w: Reuse) -> None:
    [first, second, third] = w.subs
    for sub in w.subs:
        w.carry(sub)
    with w.sessions() as s:  # the copy has test data of its own now, with one more case
        dataset = DatasetRow(
            tenant_id=TENANT,
            project_id=w.p.w.project,
            sub_skill_id=w.copies[first],
            version=1,
            sha256="e" * 64,
        )
        s.add(dataset)
        s.flush()
        for name, kind, terminal in (
            ("happy", "PASS_FIRST", "SUCCESS"),
            ("noop", "NO_OP", "SUCCESS"),
            ("stuck", "ESCALATION", "ESCALATED"),
            ("extra", "PASS_FIRST", "SUCCESS"),
        ):
            s.add(
                CaseRow(
                    tenant_id=TENANT,
                    project_id=w.p.w.project,
                    dataset_id=dataset.id,
                    name=name,
                    kind=kind,
                    input={},
                    expected_terminal=terminal,
                    assertions=[],
                )
            )
        s.commit()
    assert len(w.run()) == 2
    assert refused_with(w, first)["differing"] == ["suite_sha256"]
    assert w.certificate_of(w.copies[second]) is not None
    assert w.certificate_of(w.copies[third]) is not None


def test_e14_3_t04_a_new_template_version_reuses_nothing(
    w: Reuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    for sub in w.subs:
        w.carry(sub)
    newer = build_manifest_module._template().model_copy(
        update={"version": "1.0.2", "sha256": "f" * 64}
    )
    monkeypatch.setattr(build_manifest_module, "_template", lambda: newer)
    assert w.run() == []
    for sub in w.subs:
        assert refused_with(w, sub)["differing"] == ["template"]


def test_e14_3_a_renamed_sub_skill_is_a_change(w: Reuse) -> None:
    [first, *_] = w.subs
    copied = w.carry(first)
    with w.sessions() as s:
        row = s.get(SubSkill, copied)
        assert row is not None
        isatvon = copy.deepcopy(dict(row.isatvon))
        isatvon["skill_metadata"]["skill_name"] = "Renamed by the FDE"
        row.isatvon = isatvon
        s.commit()
    assert w.run() == []
    assert "skill_name" in refused_with(w, first)["differing"]


# --- T05: the source's evidence is re-verified before anything is reused ------------------------


def test_e14_3_t05_a_tampered_bundle_refuses_reuse_and_raises_the_alert(w: Reuse) -> None:
    [first, second, _] = w.subs
    for sub in w.subs:
        w.carry(sub)
    key = f"code/{first}"
    w.storage.objects[key] = b"not the certified tar"
    assert len(w.run()) == 2
    row = refused_with(w, first)
    assert row["reason"] == reuse.EVIDENCE_TAMPERED and not row["evidence_verified"]
    [alert] = w.audits(TAMPER_ACTION)
    assert alert.entity_id == str(w.copies[first])
    assert w.certificate_of(w.copies[second]) is not None  # the others are not affected


def test_e14_3_t05_a_file_changed_inside_the_stored_bundle_is_tamper(w: Reuse) -> None:
    [first, *_] = w.subs
    w.carry(first)
    bundle = make_bundle({"action.py": b"# changed after certification", "loop_spec.json": b"{}"})
    w.storage.objects[f"code/{first}"] = bundle.tar
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse.EVIDENCE_TAMPERED
    assert len(w.audits(TAMPER_ACTION)) == 1


def test_e14_3_t05_a_missing_bundle_is_tamper_too(w: Reuse) -> None:
    [first, *_] = w.subs
    w.carry(first)
    del w.storage.objects[f"code/{first}"]
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse.EVIDENCE_TAMPERED


def test_e14_3_t05_a_certificate_whose_hash_no_longer_matches_its_attempt_is_tamper(
    w: Reuse,
) -> None:
    [first, *_] = w.subs
    w.carry(first)
    with w.sessions() as s:
        attempt = s.get(CodeAttempt, w.attempts[first])
        assert attempt is not None
        attempt.bundle_sha256 = "0" * 64
        s.commit()
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse.EVIDENCE_TAMPERED


def test_e14_3_a_local_docker_run_never_certifies_so_never_reuses(w: Reuse) -> None:
    [first, second, _] = w.subs
    for sub in w.subs:
        w.carry(sub)
    with w.sessions() as s:
        s.execute(
            update(SandboxRunRecord)
            .where(SandboxRunRecord.id == w.sandboxes[first])
            .values(mode="LOCAL_DOCKER_NONCERT")
        )
        s.commit()
    assert len(w.run()) == 2
    row = refused_with(w, first)
    assert row["reason"] == reuse_step.NOT_CERTIFYING
    assert w.audits(TAMPER_ACTION) == []  # not tamper: it never was certifying
    assert w.certificate_of(w.copies[second]) is not None


# --- tenant and project scope ------------------------------------------------------------------


def test_e14_3_a_source_in_another_project_is_never_found(w: Reuse) -> None:
    [first, *_] = w.subs
    w.carry(first)
    with w.sessions() as s:
        s.execute(update(SubSkill).where(SubSkill.id == first).values(project_id=uuid.uuid4()))
        s.commit()
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse_step.NO_SOURCE


def test_e14_3_a_certificate_in_another_project_is_never_found(w: Reuse) -> None:
    [first, *_] = w.subs
    w.carry(first)
    with w.sessions() as s:
        thaw(s)
        s.execute(
            update(CertificationRow)
            .where(CertificationRow.subject_id == first)
            .values(project_id=uuid.uuid4())
        )
        s.commit()
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse.NO_PRIOR_CERTIFICATE


def test_e14_3_another_tenants_run_sees_and_writes_nothing(w: Reuse) -> None:
    for sub in w.subs:
        w.carry(sub)
    models = (CertificationRow, AuditLog, SubSkillManifest)
    before = [w.count(m) for m in models]
    assert w.run(w.ref(tenant_id=uuid.uuid4())) == []
    assert before == [w.count(m) for m in models]


def test_e14_3_a_sub_skill_that_was_not_carried_over_is_left_alone(w: Reuse) -> None:
    [first, *_] = w.subs
    copied = w.carry(first, reused=False)  # never built: nothing to reuse
    assert w.run() == []
    assert w.sub(copied).state == "CERTIFIED" and w.audits(REFUSED_ACTION) == []


def test_e14_3_a_source_without_a_certificate_goes_back_to_be_built(w: Reuse) -> None:
    [first, *_] = w.subs
    w.carry(first)
    with w.sessions() as s:
        thaw(s)
        s.execute(CertificationRow.__table__.delete())  # type: ignore[attr-defined]
        s.commit()
    assert w.run() == []
    assert refused_with(w, first)["reason"] == reuse.NO_PRIOR_CERTIFICATE


# --- wiring ------------------------------------------------------------------------------------


def test_e14_3_the_stub_and_the_real_activity_share_one_name_on_the_certify_queue() -> None:
    registry.load_all()
    activities = registry.collect(TaskQueue.CERTIFY)[1]
    names = {getattr(a, "__temporal_activity_definition").name for a in activities}
    assert "reuse_certifications" in names
    assert getattr(stubs.reuse_certifications, "__temporal_activity_definition").name == STAGE


def test_e14_3_the_pipeline_calls_it_behind_its_own_patch_after_the_carry_over() -> None:
    from sbam_workers.pipeline import project_pipeline as pipeline

    assert pipeline.PLAN["reuse_certifications"][0] is TaskQueue.CERTIFY
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    carry = source.index("carry_over_workflow, carry")
    patch = source.index("workflow.patched(REUSE_CERTIFICATIONS_PATCH)")
    assert carry < patch < source.index('await self._project("workflow_created")')
