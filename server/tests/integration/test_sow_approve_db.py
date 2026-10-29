"""E7.1-T10 on the Compose Postgres: two approvals of the same version at the same time make exactly
one APPROVED transition and one signal; both callers get 200's result. Real row locks, a fake
Temporal client. A scratch database migrated to head; dropped at the end."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import CurrentUser
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.projects.models import Project
from sbam_api.sow.approve import approve_version
from sbam_api.sow.models import SowVersion
from sbam_api.sow.service import Generation, record_generation
from sbam_contracts.pipeline import PipelineStage, PipelineStatus
from sbam_contracts.sow import SowDocument
from sbam_contracts.sow_api import SowApproval, SowApproveRequest
from sqlalchemy import pool, select, text
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e71_{uuid.uuid4().hex[:10]}"
TENANT = uuid.uuid4()
FDE = CurrentUser(
    user_id=uuid.uuid4(), keycloak_sub="fde-a", tenant_id=TENANT, roles=frozenset({"sbam-fde"})
)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
DOC = SowDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8")).model_dump(mode="json")


class Pipeline:
    """Temporal for `signal_pipeline`: waiting for the approval; signals recorded, a little slow."""

    def __init__(self) -> None:
        self.signals: list[Any] = []

    def get_workflow_handle(self, workflow_id: str, **_: Any) -> "Pipeline":
        return self

    async def query(self, name: str, **_: Any) -> PipelineStatus:
        return PipelineStatus(stage=PipelineStage.WAIT_SOW_APPROVAL)

    async def signal(self, name: str, payload: Any, **_: Any) -> None:
        await asyncio.sleep(0.2)  # widen the race: the first holds the row lock meanwhile
        self.signals.append(payload)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="module")
def sessions(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def a_sow(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, str]:
    with sessions() as s:
        project = Project(tenant_id=TENANT, name=f"T10-{uuid.uuid4().hex[:6]}", status="ACTIVE")
        project.state = "SOW_REVIEW"
        s.add(project)
        s.flush()
        kv = KnowledgeVersion(
            id=uuid7(),
            tenant_id=TENANT,
            project_id=project.id,
            version=1,
            run_id=uuid7(),
            cognee_dataset="d",
            coverage={},
            state="READY",
        )
        s.add(kv)
        s.flush()
        ctx = TenantContext(TENANT, project.id)
        row = record_generation(
            s, ctx, Generation(uuid7(), kv.id, DOC, None), correlation_id="c-t10"
        )
        s.commit()
        return project.id, row.sha256 or ""


def test_e7_1_t10_concurrent_approvals_make_one_transition_and_one_signal(
    sessions: sessionmaker[Session],
) -> None:
    project_id, sha = a_sow(sessions)
    pipeline = Pipeline()

    async def client() -> Any:
        return pipeline

    async def both() -> tuple[SowApproval, SowApproval]:
        body = SowApproveRequest(sha256=sha)
        return await asyncio.gather(
            approve_version(sessions, client, FDE, project_id, 1, body),
            approve_version(sessions, client, FDE, project_id, 1, body),
        )

    first, second = asyncio.run(both())
    assert first.status.value == second.status.value == "APPROVED"
    assert first.sha256 == second.sha256 == sha
    assert len(pipeline.signals) == 1
    with sessions() as s:
        version = s.scalars(select(SowVersion).where(SowVersion.project_id == project_id)).one()
        assert version.state == "APPROVED"
        approvals = s.scalars(
            select(AuditLog).where(
                AuditLog.action == "SOW_APPROVED", AuditLog.entity_id == str(version.id)
            )
        ).all()
        assert len(approvals) == 1
