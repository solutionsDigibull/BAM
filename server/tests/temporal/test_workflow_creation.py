"""E8.1 on Temporal's time-skipping server: the ProjectPipeline with the real `create_workflow`
activity and the real projects / SOW / Workflow tables (SQLite). The other stages are the usual
stand-ins; `extract_macro_skills` also records what the database shows while it runs."""

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from sbam_api.audit.models import AuditLog
from sbam_api.projects.models import Project
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import IdsResult, PipelineStage, SowApproved, WorkflowRef
from sbam_workers import mirror
from sbam_workers.stub_ids import stub_id
from sbam_workers.workflows.create import create_workflow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from test_project_pipeline import FDE, TENANT, environment, start, until
from workflows_db import add_project, real_models, sow_version, workflow_sessions, workflows

SEEN: list[tuple[str | None, str | None]] = []  # (Workflow state, project state) during extraction


@activity.defn(name="extract_macro_skills")
def extracting(inp: WorkflowRef) -> IdsResult:
    with mirror.sessions()() as s:
        row = s.get(Workflow, inp.workflow_id)
        project = s.get(Project, inp.project_id)
        SEEN.append((row.state if row else None, project.state if project else None))
    return IdsResult(ids=[stub_id(inp, f"macro:{n}") for n in (1, 2)])


REAL: dict[str, Callable[..., Any]] = {
    "create_workflow": create_workflow,
    "extract_macro_skills": extracting,
}


def run(scenario: Callable[[sessionmaker[Session]], Coroutine[Any, Any, Any]]) -> Any:
    sessions = workflow_sessions()
    mirror.configure(sessions)
    SEEN.clear()
    with real_models():
        return asyncio.run(scenario(sessions))


def approval(version: uuid.UUID, sha: str) -> SowApproved:
    return SowApproved(sow_version_id=version, sha256=sha, actor_id=FDE)


def project_state(sessions: sessionmaker[Session], project: uuid.UUID) -> str:
    with sessions() as s:
        row = s.get(Project, project)
        assert row is not None
        return row.state


def test_e8_1_t01_the_workflow_is_created_after_approval() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = add_project(sessions, TENANT)
        async with environment(REAL) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            version, sha = sow_version(sessions, TENANT, project)
            await handle.signal("sow_approved", approval(version, sha))
            await until(handle, PipelineStage.WAIT_GENERATE)
            await handle.terminate("test done")
        (row,) = workflows(sessions, project)
        assert (row.version, row.major, row.created_by) == ("1.0.0", 1, "SYSTEM")
        assert row.sow_version_id == version
        assert row.name == "EMS RFQ costing v1"
        assert SEEN[0] == ("ISATVON_EXTRACTING", "WORKFLOW_CREATED")
        with sessions() as s:
            created = s.scalars(
                select(AuditLog).where(
                    AuditLog.entity_type == "workflow", AuditLog.entity_id == str(row.id)
                )
            ).first()
        assert created is not None and created.actor_id == "SYSTEM"
        assert created.after is not None and created.after["state"] == "CREATED"

    run(scenario)


def test_e8_1_t02_no_workflow_and_no_extraction_before_approval() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = add_project(sessions, TENANT)
        async with environment(REAL) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await env.sleep(60 * 60)  # time-skipping: an hour without a signal
            status = await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.terminate("test done")
        assert status.stage is PipelineStage.WAIT_SOW_APPROVAL
        assert project_state(sessions, project) == "SOW_REVIEW"
        assert workflows(sessions, project) == []
        assert SEEN == []  # extract_macro_skills never ran

    run(scenario)


def test_e8_1_t04_a_duplicate_signal_makes_one_workflow() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = add_project(sessions, TENANT)
        async with environment(REAL) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            version, sha = sow_version(sessions, TENANT, project)
            await handle.signal("sow_approved", approval(version, sha))
            await handle.signal("sow_approved", approval(version, sha))
            await until(handle, PipelineStage.WAIT_GENERATE)
            await handle.terminate("test done")
        assert len(workflows(sessions, project)) == 1
        assert len(SEEN) == 1

    run(scenario)


def test_e8_1_t05_a_hash_mismatch_creates_nothing_and_fails_the_stage() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = add_project(sessions, TENANT)
        async with environment(REAL) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            version, _ = sow_version(sessions, TENANT, project)
            await handle.signal("sow_approved", approval(version, "cd" * 32))
            status = await until(handle, PipelineStage.FAILED)
        assert status.last_error == "create_workflow: HashMismatch"
        assert status.failed_stage is PipelineStage.CREATE_WORKFLOW
        assert workflows(sessions, project) == []
        assert project_state(sessions, project) == "SOW_APPROVED"
        assert SEEN == []

    run(scenario)
