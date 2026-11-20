"""E14.2 `certify_macros`, `validate_workflow` and `certify_workflow` on Compose Postgres (#130).

The unit tests (`tests/unit/test_e14_2_certify_workflow.py`) run the same stages on SQLite. Here the
real `WorkflowCertification` runs with the real repositories, the real state-machine service and a
real `LocalDiskBackend` against a scratch database migrated to head: the happy path writes two
MACRO_SKILL certificates, moves both Agents to CERTIFIED and writes the WORKFLOW certificate on the
newest manifest hash, which is the row Commit's recheck finds. Every certificate is frozen by the
shared `forbid_frozen_change` trigger. Needs `make up`; not part of the unit run.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.projects.models import Project
from sbam_api.sow.service import Generation, record_generation
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.sow import SowDocument
from sbam_workers.build.dag import Node
from sbam_workers.build.workflow_manifest import Agent, DraftInputs, Skill, draft
from sbam_workers.certify.workflow import SqlFacts, WorkflowCertification
from sqlalchemy import pool, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
DIGEST = "sha256:" + "34" * 32
SOW_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
SQL = (
    "CREATE TABLE investors (id uuid PRIMARY KEY, email text NOT NULL);\n"
    "CREATE TABLE quotes (id uuid PRIMARY KEY, amount numeric);\n"
)
SKILLS: dict[str, tuple[str, list[str], str | None]] = {
    "MS01.1_Login": ("MS01_Access", ["investors"], "MS01.2_Verify"),
    "MS01.2_Verify": ("MS01_Access", [], "MS02_Quotes"),
    "MS02.1_Quote": ("MS02_Quotes", ["quotes"], None),
}


@contextmanager
def scratch_db(prefix: str) -> Iterator[sessionmaker[Session]]:
    """A database of its own on the stack's Postgres, migrated to head and dropped afterwards."""
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" OWNER sbam'))
    engine = make_engine(url(database=name), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=name))
            command.upgrade(config(), "head")
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    with scratch_db("sbam_e142") as made:
        yield made


def isatvon(skill_id: str) -> dict[str, Any]:
    _, tables, nxt = SKILLS[skill_id]
    return {
        "skill_metadata": {"skill_id": skill_id},
        "isatvon_definition": {"v_variables": {"tables": tables}, "n_notification": {"next": nxt}},
    }


def bundle_of(skill_id: str) -> str:
    return hashlib.sha256(skill_id.encode()).hexdigest()


def seed(s: Session, storage: LocalDiskBackend) -> tuple[WorkflowRef, dict[str, uuid.UUID], str]:
    """A Workflow in CERTIFYING, 2 Macros (PENDING), 3 Sub-Skills certified on their bundles, the
    stored schema.sql and the manifest draft. Returns the run ref, the Macro ids and its hash."""
    tenant = uuid.uuid4()
    project = Project(tenant_id=tenant, name=f"it-{uuid.uuid4().hex[:8]}", status="ACTIVE")
    project.state = "WORKFLOW_CREATED"
    s.add(project)
    s.flush()
    ctx = TenantContext(tenant, project.id)
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
        s, ctx, Generation(uuid7(), kv.id, doc, None), correlation_id="cid-it"
    )
    workflow = Workflow(
        id=uuid7(),
        tenant_id=tenant,
        project_id=project.id,
        sow_version_id=version.id,
        major=1,
        version="1.0.0",
        name="it",
        created_by="SYSTEM",
    )
    workflow.state = "CERTIFYING"
    s.add(workflow)
    s.flush()
    macros: dict[str, uuid.UUID] = {}
    for order, category in enumerate(("MS01_Access", "MS02_Quotes"), start=1):
        macro = MacroSkill(
            id=uuid7(),
            tenant_id=tenant,
            project_id=project.id,
            workflow_id=workflow.id,
            sow_version_id=version.id,
            macro_category=category,
            name=category,
            description="-",
            order=order,
            sow_refs=["M-ACCESS"],
            agent_ref=None,
            validation_errors=[],
        )
        macro.state = "PENDING"
        s.add(macro)
        macros[category] = macro.id
    s.flush()
    skills = []
    for order, skill_id in enumerate(SKILLS, start=1):
        sub = SubSkill(
            id=uuid7(),
            tenant_id=tenant,
            project_id=project.id,
            macro_skill_id=macros[SKILLS[skill_id][0]],
            workflow_id=workflow.id,
            skill_id=skill_id,
            skill_name=skill_id,
            order=order,
            isatvon=isatvon(skill_id),
            isatvon_framework_version="1.4",
            sow_refs=["F-ACCESS-1"],
            validation_errors=[],
            content_sha256="0" * 64,
        )
        sub.state = "CERTIFIED"
        s.add(sub)
        s.flush()
        s.add(
            CodeAttempt(
                id=uuid7(),
                tenant_id=tenant,
                project_id=project.id,
                workflow_id=workflow.id,
                sub_skill_id=sub.id,
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
                bundle_sha256=bundle_of(skill_id),
                created_by="SYSTEM",
            )
        )
        s.add(
            CertificationRow(
                id=uuid7(),
                tenant_id=tenant,
                project_id=project.id,
                level="SUB_SKILL",
                subject_id=sub.id,
                content_sha256=f"sha256:{bundle_of(skill_id)}",
                evidence={},
                certified_at=NOW,
                created_by="SYSTEM",
            )
        )
        skills.append(
            Skill(
                node=Node(sub.id, skill_id, SKILLS[skill_id][0], isatvon(skill_id)),
                manifest_sha256="sha256:" + "a" * 64,
                bundle_sha256=bundle_of(skill_id),
            )
        )
    stored = storage.put(
        SQL.encode(), StorageScope(tenant, project.id, workflow.id), Kind.CODE, "application/sql"
    )
    manifest = draft(
        DraftInputs(
            ids={
                "tenant_id": str(tenant),
                "project_id": str(project.id),
                "workflow_id": str(workflow.id),
            },
            name="it",
            objective="-",
            version="1.0.0",
            previous_version=None,
            provenance={
                "sow_version_id": str(version.id),
                "sow_sha256": "ab" * 32,
                "approved_at": "2026-10-01T10:00:00Z",
                "approved_by": "fde",
                "knowledge_version_id": str(kv.id),
                "okf_sha256": "cd" * 32,
                "master_snapshot_sha256": "sha256:" + "ef" * 32,
                "sow_refs": ["M-ACCESS"],
            },
            agents=[Agent("MS01_Access", "Access", None), Agent("MS02_Quotes", "Quotes", None)],
            skills=skills,
            schema_sql=(stored.storage_key, hashlib.sha256(SQL.encode()).hexdigest()),
            runtime_digest=DIGEST,
        )
    )
    s.add(
        WorkflowManifestRow(
            id=uuid7(),
            tenant_id=tenant,
            project_id=project.id,
            workflow_id=workflow.id,
            build_no=1,
            manifest=json.loads(json.dumps(manifest.dump())),
            sha256=manifest.integrity.content_sha256,
            created_by="SYSTEM",
        )
    )
    s.commit()
    ref = WorkflowRef(
        tenant_id=tenant,
        project_id=project.id,
        run_id=uuid.uuid4(),
        correlation_id="cid-e14-2-it",
        workflow_id=workflow.id,
    )
    return ref, macros, manifest.integrity.content_sha256


def test_e14_2_happy_path_certifies_two_macros_and_the_workflow_db(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = LocalDiskBackend(tmp_path)
    with sessions() as s:
        ref, macros, manifest_sha = seed(s, storage)
    stages = WorkflowCertification(sessions, SqlFacts(storage), clock=lambda: NOW)

    macro_ids = asyncio.run(stages.run_certify_macros(ref)).ids
    assert len(macro_ids) == 2
    assert sorted(asyncio.run(stages.run_certify_macros(ref)).ids) == sorted(macro_ids)  # retry
    with sessions() as s:
        states = {
            m.macro_category: m.state
            for m in s.scalars(select(MacroSkill))
            if m.id in macros.values()
        }
    assert states == {"MS01_Access": "CERTIFIED", "MS02_Quotes": "CERTIFIED"}

    manifest_id = asyncio.run(stages.run_validate_workflow(ref)).id
    cert_id = asyncio.run(stages.run_certify_workflow(ref)).id
    assert asyncio.run(stages.run_certify_workflow(ref)).id == cert_id  # retry

    ctx = TenantContext(ref.tenant_id, ref.project_id)
    with sessions() as s:
        newest = s.get(WorkflowManifestRow, manifest_id)
        assert newest is not None and newest.sha256 == manifest_sha
        found = CertificationRepository(s, ctx).find(
            CertificationLevel.WORKFLOW.value, ref.workflow_id, newest.sha256
        )  # what workflow_commit.checks._certified looks for
        assert found is not None and found.id == cert_id and found.is_frozen
        rows = s.scalars(
            select(CertificationRow).where(
                CertificationRow.tenant_id == ref.tenant_id,
                CertificationRow.level == CertificationLevel.MACRO_SKILL.value,
            )
        )
        assert sorted(r.id for r in rows) == sorted(macro_ids)
    with sessions() as s, pytest.raises(DBAPIError) as err:
        s.execute(
            text("UPDATE sbam.certifications SET evidence = '{}' WHERE id = :i"), {"i": cert_id}
        )
        s.commit()
    assert getattr(err.value.orig, "sqlstate", None) == "SB423"  # as test_db.py checks it
