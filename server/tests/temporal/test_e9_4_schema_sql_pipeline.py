"""E9.4-T09: `build_schema_sql` in the pipeline. A transient failure (the schema-check PostgreSQL
not up yet) is retried and the build goes on; SchemaInvalid is non-retryable and stops the
pipeline with an FDE notification. The stage itself: tests/unit/test_e9_4_schema_sql.py."""

import uuid
from collections.abc import Coroutine
from typing import Any

from pipeline_db import actions, new_project
from sbam_contracts.pipeline import IdResult, PipelineStage, PipelineStatus, WorkflowRef
from sbam_workers.stub_ids import stub_id
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError
from test_project_pipeline import (
    TENANT,
    approve,
    environment,
    finish,
    generate,
    run,
    start,
    until,
)


def drive(overrides: dict[str, Any]) -> tuple[uuid.UUID, PipelineStatus, sessionmaker[Session]]:
    holder: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus]:
        holder["s"] = sessions
        project = new_project(sessions, TENANT)
        async with environment(overrides) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            return project, await finish(handle)

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        return scenario(sessions)

    project, status = run(wrapped)
    return project, status, holder["s"]


def test_e9_4_t09_a_transient_failure_is_retried_once_then_the_build_goes_on() -> None:
    calls = {"n": 0}

    @activity.defn(name="build_schema_sql")
    async def flaky(inp: WorkflowRef) -> IdResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("schema-check is not accepting connections yet")
        return IdResult(id=stub_id(inp, "schema_sql"))

    _, status, _ = drive({"build_schema_sql": flaky})
    assert calls["n"] == 2
    assert status.stage is PipelineStage.DONE


def test_e9_4_t09_schema_invalid_is_not_retried_and_notifies_the_fde() -> None:
    calls = {"n": 0}

    @activity.defn(name="build_schema_sql")
    async def invalid(inp: WorkflowRef) -> IdResult:
        calls["n"] += 1
        raise ApplicationError("FORBIDDEN_STATEMENT", type="SchemaInvalid", non_retryable=True)

    project, status, sessions = drive({"build_schema_sql": invalid})
    assert calls["n"] == 1
    assert status.stage is PipelineStage.FAILED
    assert status.failed_stage is PipelineStage.BUILD_SCHEMA_SQL
    assert status.last_error == "build_schema_sql: SchemaInvalid"
    assert "pipeline.failed" in actions(sessions, "project", project)
