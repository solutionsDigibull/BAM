"""E4.1-T09: the API's start and ProjectPipeline agree on who applies the first transition.

Real `start_project_pipeline` (real projects / document tables in SQLite) against the real workflow
on Temporal's time-skipping server. The API moves the project IDLE -> INGESTING (or INGESTION_FAILED
-> INGESTING) after Temporal accepts the start, with the key the workflow's own first activity
uses, so the activity must replay it: one transition row, and a restart after a failure is not
rejected by the workflow's `start_processing` (which that state does not allow).
"""

import asyncio
import dataclasses
import threading
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from pipeline_db import WORKFLOW_ROW, workflow_rows
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import ROLE_FDE, CurrentUser
from sbam_api.core.ids import uuid7
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.pipeline.service import pipeline_status, start_project_pipeline
from sbam_api.projects.models import Project
from sbam_api.state_machine.registry import register_entity, unregister_entity
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.pipeline import Cancel, PipelineStage, TransitionOutcome, TransitionRequest
from sbam_contracts.state_machines import MachineName
from sbam_workers import mirror
from sbam_workers.pipeline_state import apply_pipeline_transition
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from state_db import sqlite_sessions
from temporalio import activity
from test_project_pipeline import FDE, TENANT, environment, until

USER = CurrentUser(
    user_id=uuid.UUID(FDE),
    keycloak_sub="sub",
    tenant_id=TENANT,
    roles=frozenset({ROLE_FDE}),
    assigned_project_ids=frozenset(),
)


def sessions_with_tables() -> sessionmaker[Session]:
    sessions = sqlite_sessions()
    engine = sessions.kw["bind"]
    for table in (Project, Document, DocumentVersion, TemporalRun):
        table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return sessions


def seed(sessions: sessionmaker[Session], state: str) -> uuid.UUID:
    project_id = uuid7()
    with sessions() as session:
        session.add(Project(id=project_id, tenant_id=TENANT, name="P", state=state))
        doc = Document(tenant_id=TENANT, project_id=project_id, title="a.pdf")
        session.add(doc)
        session.flush()
        session.add(
            DocumentVersion(
                tenant_id=TENANT,
                project_id=project_id,
                document_id=doc.id,
                storage_key="k",
                sha256="0" * 64,
                size=1,
                mime="application/pdf",
                state="UPLOADED",
            )
        )
        session.commit()
    return project_id


def audit_trail(sessions: sessionmaker[Session], project: uuid.UUID) -> list[AuditLog]:
    with sessions() as session:
        query = select(AuditLog).where(
            AuditLog.entity_type == "project_pipeline", AuditLog.entity_id == str(project)
        )
        return list(session.scalars(query.order_by(AuditLog.id)))


def run(scenario: Callable[[sessionmaker[Session]], Coroutine[Any, Any, Any]]) -> Any:
    sessions = sessions_with_tables()
    mirror.configure(sessions)
    # Other temporal tests unregister the projects table on exit (their own fake rows); import
    # registered it, so put it back. The real Workflow table is #91's.
    register_entity(MachineName.PROJECT_PIPELINE, Project)
    register_entity(MachineName.WORKFLOW, WORKFLOW_ROW)
    try:
        return asyncio.run(scenario(sessions))
    finally:
        unregister_entity(MachineName.WORKFLOW)


def after_commit(committed: threading.Event) -> Callable[..., Any]:
    """The workflow's transition activity waits for the API's commit, as it does on Postgres
    where the API holds the project row lock (SELECT ... FOR UPDATE) until then. SQLite has no
    row locks, so without this the activity could read the row before the API's write lands."""

    @activity.defn(name="apply_pipeline_transition")
    def gated(req: TransitionRequest) -> TransitionOutcome:
        assert committed.wait(30)
        return apply_pipeline_transition(req)

    return gated


def start_and_wait_for_sow(initial_state: str) -> tuple[list[AuditLog], list[Any], str]:
    seen: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = seed(sessions, initial_state)
        user = dataclasses.replace(USER, assigned_project_ids=frozenset({project}))
        committed = threading.Event()
        async with environment({"apply_pipeline_transition": after_commit(committed)}) as env:
            started = await start_project_pipeline(
                sessions, _client(env), user, project, with_search_attributes=False
            )
            committed.set()
            handle = env.client.get_workflow_handle(f"pipeline-{project}")
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            status = await pipeline_status(env.client, project)
            seen.update(project=project, started=started, status=status, sessions=sessions)
            await handle.signal("cancel", Cancel(reason="test done", actor_id=FDE))
            await handle.result()

    run(scenario)
    sessions = seen["sessions"]
    return (
        audit_trail(sessions, seen["project"]),
        workflow_rows(sessions, seen["project"]),
        (seen["started"].temporal_run_id),
    )


def _client(env: Any) -> Callable[[], Any]:
    async def get() -> Any:
        return env.client

    return get


def test_e4_1_t09_started_pipeline_advances_and_creates_no_workflow_yet() -> None:
    trail, workflows, run_id = start_and_wait_for_sow("IDLE")
    trail = trail[:-1]  # the test's own cancel at the end
    assert [r.action for r in trail] == [
        "start_processing",
        "ingestion_completed",
        "knowledge_ready",
        "sow_generated",
    ]  # the workflow's own start_processing replayed the API's: one row, not two
    assert trail[0].actor_id == FDE and (trail[0].idempotency_key or "").startswith(run_id)
    assert [(r.after or {}).get("state") for r in trail] == [
        "INGESTING",
        "KNOWLEDGE_BUILDING",
        "SOW_GENERATING",
        "SOW_REVIEW",
    ]
    assert workflows == []  # no Workflow row before sow_approved


def test_e4_1_restart_after_ingestion_failed_is_not_rejected_by_the_workflow() -> None:
    trail, _, _ = start_and_wait_for_sow("INGESTION_FAILED")
    assert [r.action for r in trail][:2] == ["retry", "ingestion_completed"]
    assert (trail[-2].after or {}).get(
        "state"
    ) == "SOW_REVIEW"  # it ran on to the SOW wait, then was cancelled
