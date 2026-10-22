"""E6.4 on Temporal's time-skipping server: `SowExport` runs `export_sow` once and returns its
result; a refusal (`ExportNotAllowed`) is not retried."""

import uuid
from typing import Any

import pytest
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow_api import (
    SOW_EXPORT,
    ExportFormat,
    SowExportInput,
    SowExportResult,
    sow_export_workflow_id,
)
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from test_project_pipeline import TENANT, environment, run

RESULT = SowExportResult(storage_key="k", sha256="0" * 64, size=3)


def export_input() -> SowExportInput:
    return SowExportInput(
        tenant_id=TENANT,
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        correlation_id="cid-e6-4",
        sow_version_id=uuid.uuid4(),
        format=ExportFormat.PDF,
    )


def stand_in(calls: list[SowExportInput], refuse: bool = False) -> Any:
    @activity.defn(name="export_sow")
    async def export(inp: SowExportInput) -> SowExportResult:
        calls.append(inp)
        if refuse:
            raise ApplicationError("failed generation", type="ExportNotAllowed", non_retryable=True)
        return RESULT

    return export


def test_e6_4_sow_export_returns_the_activity_result() -> None:
    calls: list[SowExportInput] = []

    async def scenario(_: Any) -> None:
        inp = export_input()
        async with environment({"export_sow": stand_in(calls)}) as env:
            result = await env.client.execute_workflow(
                SOW_EXPORT,
                inp,
                id=sow_export_workflow_id(inp.sow_version_id, inp.format),
                task_queue=TaskQueue.BUILD.value,
                result_type=SowExportResult,
            )
        assert result == RESULT and [c.format for c in calls] == [ExportFormat.PDF]

    run(scenario)


def test_e6_4_a_refused_export_is_not_retried() -> None:
    calls: list[SowExportInput] = []

    async def scenario(_: Any) -> None:
        inp = export_input()
        async with environment({"export_sow": stand_in(calls, refuse=True)}) as env:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    SOW_EXPORT,
                    inp,
                    id=sow_export_workflow_id(inp.sow_version_id, inp.format),
                    task_queue=TaskQueue.BUILD.value,
                    result_type=SowExportResult,
                )
        assert len(calls) == 1

    run(scenario)
