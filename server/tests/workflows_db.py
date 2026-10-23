"""Test helpers for E8.1: temp-file SQLite with the real projects, SOW, Workflow, run mirror and
audit/outbox tables, and an approved SOW version to create the Workflow from."""

import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.notifications.models import Notification
from sbam_api.projects.models import Project
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.sow.service import canonical_sha256
from sbam_api.state_machine.registry import entity_model, register_entity, unregister_entity
from sbam_api.temporal.models import TemporalRun
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import CreateWorkflowInput
from sbam_contracts.state_machines import MachineName
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

FIXTURE = Path(__file__).parent / "fixtures" / "sow" / "ms01_1_sow.json"
STRUCTURED: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


@contextmanager
def real_models() -> Iterator[None]:
    """The real projects and Workflow tables for the pipeline's transitions, whatever another test
    registered before; the previous registrations come back afterwards."""
    machines: dict[MachineName, type[Any]] = {
        MachineName.PROJECT_PIPELINE: Project,
        MachineName.WORKFLOW: Workflow,
    }
    before = {m: entity_model(m) for m in machines}
    for machine, model in machines.items():
        register_entity(machine, model)
    try:
        yield
    finally:
        for machine, previous in before.items():
            unregister_entity(machine)
            if previous is not None:
                register_entity(machine, previous)


def workflow_sessions() -> sessionmaker[Session]:
    """A file, not `:memory:`: Temporal's activity threads each get their own connection."""
    fd, path = tempfile.mkstemp(prefix="sbam-wf-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    tables = (Project, Sow, SowVersion, Workflow, TemporalRun, AuditLog, NotificationOutbox)
    for model in (*tables, Notification):
        model.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return sessionmaker(engine, expire_on_commit=False)


def add_project(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID,
    name: str = "EMS RFQ costing",
    state: str = "IDLE",
) -> uuid.UUID:
    with sessions() as s:
        project = Project(tenant_id=tenant, name=name, status="ACTIVE")
        project.state = state
        s.add(project)
        s.commit()
        return project.id


def sow_version(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID,
    project: uuid.UUID,
    *,
    approved: bool = True,
    structured: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, str]:
    """The project's next SOW version; `approved` makes it the SOW's approved one (the previous
    approved version is SUPERSEDED, as E7.1 does). Returns its id and sha256."""
    doc = structured if structured is not None else STRUCTURED
    sha = canonical_sha256(doc)
    with sessions() as s:
        sow = s.scalars(select(Sow).where(Sow.project_id == project)).one_or_none()
        if sow is None:
            sow = Sow(tenant_id=tenant, project_id=project)
            s.add(sow)
            s.flush()
        number = len(s.scalars(select(SowVersion).where(SowVersion.sow_id == sow.id)).all()) + 1
        version = SowVersion(
            tenant_id=tenant,
            project_id=project,
            sow_id=sow.id,
            version=number,
            source="GENERATED",
            run_id=uuid7(),
            knowledge_version_id=uuid7(),
            structured=doc,
            sha256=sha,
        )
        version.state = "APPROVED" if approved else "GENERATED"
        s.add(version)
        s.flush()
        if approved:
            if sow.approved_version_id is not None:
                previous = s.get(SowVersion, sow.approved_version_id)
                if previous is not None:
                    previous.state = "SUPERSEDED"
            sow.approved_version_id = version.id
        sow.current_version_id = version.id
        s.commit()
        return version.id, sha


def create_input(
    tenant: uuid.UUID,
    project: uuid.UUID,
    version: uuid.UUID,
    sha: str,
    run: uuid.UUID | None = None,
) -> CreateWorkflowInput:
    return CreateWorkflowInput(
        tenant_id=tenant,
        project_id=project,
        run_id=run or uuid.uuid4(),
        correlation_id="cid-e8-1",
        sow_version_id=version,
        sha256=sha,
    )


def workflows(sessions: sessionmaker[Session], project: uuid.UUID) -> list[Workflow]:
    with sessions() as s:
        return list(
            s.scalars(
                select(Workflow).where(Workflow.project_id == project).order_by(Workflow.major)
            )
        )
