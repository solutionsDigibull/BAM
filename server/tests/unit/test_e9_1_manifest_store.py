"""E9.1: the `build_manifest` stage against the E8.3 world (SQLite): a Sub-Skill from ISATVON
pass 2, its approved SOW, knowledge version and master snapshot, and an active runtime registry."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import TENANT, World
from llm_db import FakeBifrost, completion
from registry_db import sample_registry
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import RowFrozen, TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.manifests.models import GateResultRow, SubSkillManifest
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.masters.models import MasterSnapshot
from sbam_api.registry.models import RuntimeRegistryPackageRow, RuntimeRegistryRow
from sbam_api.registry.repository import upsert_registry
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.sow.models import SowVersion
from sbam_api.srim_defaults.models import TenantSrimDefaults
from sbam_contracts.build import BuildStageInput
from sbam_contracts.canonical import deterministic_hash
from sbam_contracts.manifest import ISA14_SRIM_DEFAULTS, SkillManifest
from sbam_workers.build.manifest import (
    IDS_MISMATCH,
    MANIFEST_INVALID,
    PROVENANCE_MISSING,
    SqlManifestStore,
    build_for,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DatabaseError
from sub_skills_db import Pass2, answer, proposal
from temporalio.exceptions import ApplicationError

DIGEST = "sha256:" + "34" * 32


class Build:
    """Pass 2 run once (one VALID Sub-Skill), plus the provenance the manifest records."""

    def __init__(self, tmp: Path, **changes: object) -> None:
        self.p = Pass2(World(tmp))
        engine = self.p.w.sessions.kw["bind"]
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
        ):
            table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        skill = proposal("Investor Auth Manager", "F-ACCESS-1", "F-ACCESS-2", **changes)
        [self.sub_skill_id] = self.p.run_macro(FakeBifrost([completion(answer(skill))]))
        with self.p.w.sessions() as s:
            s.execute(  # what the real approval (sow/approve.py) records
                update(SowVersion)
                .where(SowVersion.id == self.p.version)
                .values(approved_at=datetime.now(UTC), approved_by="fde-1")
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
        self.store = SqlManifestStore(self.p.w.sessions)

    def inp(self, **kw: object) -> BuildStageInput:
        values: dict[str, object] = {
            **self.p.inp.model_dump(),
            "workflow_id": self.p.workflow,
            "sub_skill_id": self.sub_skill_id,
            "build_no": 1,
        }
        return BuildStageInput.model_validate(values | kw)

    def manifests(self) -> list[SubSkillManifest]:
        with self.p.w.sessions() as s:
            rows = list(s.scalars(select(SubSkillManifest)))
            s.expunge_all()
            return rows

    def gates(self) -> list[GateResultRow]:
        with self.p.w.sessions() as s:
            rows = list(s.scalars(select(GateResultRow)))
            s.expunge_all()
            return rows


@pytest.fixture
def b(tmp_path: Path) -> Iterator[Build]:
    yield Build(tmp_path)


def test_e9_1_stage_writes_the_manifest_with_its_provenance(b: Build) -> None:
    result = build_for(b.store, b.inp())
    [row] = b.manifests()
    assert row.id == result.id and not row.is_frozen
    manifest = SkillManifest.model_validate(row.manifest)
    assert row.sha256 == manifest.governance.deterministic_hash == deterministic_hash(row.manifest)
    record = manifest.sbam_build_record
    assert record.ids.sub_skill_id == str(b.sub_skill_id)
    assert record.ids.tenant_id == str(TENANT)
    assert record.provenance.sow_refs == ["F-ACCESS-1", "F-ACCESS-2"]
    assert record.provenance.okf_sha256 == "cd" * 32
    assert record.build.runtime_image_digest == DIGEST == row.runtime_digest
    assert record.build.template.name == "fastmcp-loopy"
    with b.p.w.sessions() as s:
        sub = s.get(SubSkill, b.sub_skill_id)
        assert sub is not None and sub.validated_at is not None
        stamp = sub.validated_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    assert manifest.skill_metadata.created_at == stamp  # never the wall clock


def test_e9_1_t08_a_retried_activity_returns_the_same_row(b: Build) -> None:
    first = build_for(b.store, b.inp())
    second = build_for(b.store, b.inp())
    assert first == second and len(b.manifests()) == 1
    other = build_for(b.store, b.inp(attempt=1))  # a new attempt is a new row, same hash
    rows = b.manifests()
    assert other.id != first.id and len({r.sha256 for r in rows}) == 1


def test_e9_1_t05_invalid_isatvon_records_a_failed_gate_and_no_manifest(tmp_path: Path) -> None:
    tool = "isatvon_definition.t_tech_stack."
    b = Build(
        tmp_path, **{f"{tool}action_tool": "verify_otp", f"{tool}fixed_check_tool": "verify_otp"}
    )
    with pytest.raises(ApplicationError) as caught:
        build_for(b.store, b.inp())
    assert caught.value.type == MANIFEST_INVALID and caught.value.non_retryable
    assert b.manifests() == []
    [gate] = b.gates()
    assert (gate.gate, gate.status, gate.sub_skill_id) == (
        "ISATVON_CONTRACT",
        "FAILED",
        b.sub_skill_id,
    )
    assert gate.findings and all(f["gate"] == "ISATVON_CONTRACT" for f in gate.findings)


def test_e9_1_t07_platform_fields_patched_into_the_isatvon_are_audited(b: Build) -> None:
    with b.p.w.sessions() as s:
        sub = s.get(SubSkill, b.sub_skill_id)
        assert sub is not None
        patched = dict(sub.isatvon)
        patched["skill_metadata"] = {**patched["skill_metadata"], "status": "APPROVED"}
        s.execute(update(SubSkill).where(SubSkill.id == sub.id).values(isatvon=patched))
        s.commit()
    build_for(b.store, b.inp())
    [row] = b.manifests()
    assert row.manifest["skill_metadata"]["status"] == "DRAFT"
    with b.p.w.sessions() as s:
        [audit] = s.scalars(
            select(AuditLog).where(AuditLog.action == "MANIFEST_PLATFORM_FIELDS_REJECTED")
        )
        assert audit.after == {
            "sub_skill_id": str(b.sub_skill_id),
            "rejected": ["skill_metadata.status"],
        }


def test_e9_1_t10_a_frozen_manifest_cannot_change(b: Build) -> None:
    build_for(b.store, b.inp())
    [row] = b.manifests()
    with b.p.w.sessions() as s:
        s.execute(update(SubSkillManifest).values(is_frozen=True))
        s.commit()
    with b.p.w.sessions() as s, pytest.raises(DatabaseError, match="frozen"):
        s.execute(update(SubSkillManifest).values(sha256="sha256:" + "00" * 32))
    with b.p.w.sessions() as s, pytest.raises(DatabaseError, match="frozen"):
        s.execute(delete(SubSkillManifest))
    assert b.manifests()[0].sha256 == row.sha256


def test_e9_1_t10_the_repository_maps_the_frozen_trigger_to_423(b: Build) -> None:
    """On Postgres the trigger raises SQLSTATE SB423, which the repository maps to RowFrozen."""

    class Sb423Error(Exception):
        sqlstate = "SB423"

    build_for(b.store, b.inp())
    with b.p.w.sessions() as s:
        repo = SubSkillManifestRepository(s, TenantContext(TENANT, b.p.w.project))
        s.flush = lambda *a, **k: (_ for _ in ()).throw(DatabaseError("UPDATE", {}, Sb423Error()))  # type: ignore[method-assign]
        with pytest.raises(RowFrozen) as caught:
            repo._flush()
    assert caught.value.status == 423


def test_e9_1_another_tenants_run_is_an_ids_mismatch(b: Build) -> None:
    with pytest.raises(ApplicationError) as caught:
        build_for(b.store, b.inp(tenant_id=uuid.uuid4()))
    assert caught.value.type == IDS_MISMATCH and b.manifests() == []


def test_e9_1_another_workflow_is_an_ids_mismatch(b: Build) -> None:
    with pytest.raises(ApplicationError) as caught:
        build_for(b.store, b.inp(workflow_id=uuid7()))
    assert caught.value.type == IDS_MISMATCH


def test_e9_1_missing_runtime_digest_is_reported(b: Build) -> None:
    with b.p.w.sessions() as s:
        s.execute(update(RuntimeRegistryRow).values(is_active=False))
        s.commit()
    with pytest.raises(ApplicationError) as caught:
        build_for(b.store, b.inp())
    assert caught.value.type == PROVENANCE_MISSING and "runtime image digest" in str(caught.value)


# --- E9.1b: the tenant's own SRIM runtime defaults ---------------------------------------------

CUSTOM: dict[str, Any] = {
    "orchestration": {"router": "Acme_Router", "trigger": "Acme_Trigger"},
    "execution": {"engine": "Acme_Engine"},
    "gateway": {"route_via": "Acme_Gateway"},
    "persistence": {"database": "Acme_DB", "action": "Acme_SQL"},
    "audit": {"telemetry": "Acme_Audit"},
    "ui": {"target": "Acme_Canvas", "method": "Acme_Widgets"},
}


def set_defaults(b: Build, tenant: uuid.UUID, defaults: dict[str, Any]) -> None:
    with b.p.w.sessions() as s:
        s.add(TenantSrimDefaults(tenant_id=tenant, defaults=defaults))
        s.commit()


def srim(row: SubSkillManifest) -> dict[str, object]:
    block: dict[str, object] = row.manifest["srim_runtime_definition"]
    return block


def test_e9_1b_t05_a_tenant_without_its_own_defaults_gets_the_isa14_ones(b: Build) -> None:
    build_for(b.store, b.inp())
    [row] = b.manifests()
    block = srim(row)
    assert block["orchestration"] == ISA14_SRIM_DEFAULTS.model_dump(mode="json")["orchestration"]
    assert block["persistence"] == {
        **ISA14_SRIM_DEFAULTS.model_dump(mode="json")["persistence"],
        "table": "investor_profiles, auth_event_audit_handoff",
    }


def test_e9_1b_t06_later_builds_use_the_tenants_values_and_earlier_manifests_stay(
    b: Build,
) -> None:
    build_for(b.store, b.inp(build_no=1))
    [first] = b.manifests()
    stored = dict(first.manifest)
    set_defaults(b, TENANT, CUSTOM)
    build_for(b.store, b.inp(build_no=2))
    rows = {r.build_no: r for r in b.manifests()}
    assert rows[1].manifest == stored and rows[1].sha256 == first.sha256  # not rewritten
    second = srim(rows[2])
    assert second["gateway"] == CUSTOM["gateway"] and second["audit"] == CUSTOM["audit"]
    assert second["persistence"] == {
        **CUSTOM["persistence"],
        "table": "investor_profiles, auth_event_audit_handoff",  # still derived from V.tables
    }
    assert rows[2].sha256 != rows[1].sha256
    SkillManifest.model_validate(rows[2].manifest)  # still a valid, sealed Skill Manifest


def test_e9_1b_t07_another_tenants_values_are_never_used(b: Build) -> None:
    set_defaults(b, uuid.uuid4(), CUSTOM)
    build_for(b.store, b.inp())
    [row] = b.manifests()
    assert srim(row)["gateway"] == ISA14_SRIM_DEFAULTS.model_dump(mode="json")["gateway"]
