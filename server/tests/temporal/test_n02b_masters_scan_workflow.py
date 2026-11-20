"""#609 on Temporal's time-skipping server: what `MastersScanImport` does with the scan activity's
answers, its retries and the document parse it hands on. The activities are fakes with the real
names; the real ones are in tests/api/test_n02b_masters_scan.py (the API and the activity together).

`RECORD_HISTORY=1` re-records `histories/masters_scan_import.json` (see `make record-histories`).
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.api import ErrorCode
from sbam_contracts.masters_scan import (
    MASTERS_SCAN_IMPORT,
    MastersScanInput,
    MastersScanKind,
    MastersScanResult,
    MastersScanState,
    masters_scan_workflow_id,
)
from sbam_contracts.pipeline import IdsResult, PipelineStage, StageRef
from sbam_contracts.runs import MirrorUpdate, RunState
from sbam_workers.ingest.master_document_ingestion import MasterDocumentIngestion
from sbam_workers.ingest.masters_scan_workflow import MastersScanImport
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

QUEUE = "sbam-ingest"
TENANT = uuid.UUID(int=1)
HISTORY = Path(__file__).parent / "histories" / "masters_scan_import.json"


@dataclass
class Fakes:
    """What the scan answers: `outcome` = done / failed, or `unavailable` for the first
    `unavailable_for` attempts; what the workflow called."""

    outcome: str = "done"
    parse_pending: bool = False
    unavailable_for: int = 0
    scans: int = 0
    gave_up: int = 0
    mirror: list[tuple[RunState, str | None, str | None]] = field(default_factory=list)
    parsed: list[uuid.UUID] = field(default_factory=list)


def scan_input(kind: MastersScanKind = MastersScanKind.IMPORT) -> MastersScanInput:
    scan_id = uuid.uuid4()
    return MastersScanInput(
        tenant_id=TENANT,
        project_id=uuid.UUID(int=0),
        run_id=uuid.uuid4(),
        correlation_id="cid-n02b",
        scan_id=scan_id,
        kind=kind,
        pending_key=f"{TENANT}/{uuid.UUID(int=0)}/{scan_id}/uploads/{'0' * 64}",
        sha256=f"sha256:{'0' * 64}",
        filename="masters.xlsx",
        user_id=uuid.uuid4(),
        keycloak_sub="admin-a",
    )


def activities(fakes: Fakes) -> list[Callable[..., Any]]:
    @activity.defn(name="scan_and_import_masters")
    async def scan_and_import_masters(inp: MastersScanInput) -> MastersScanResult:
        fakes.scans += 1
        if fakes.scans <= fakes.unavailable_for:
            raise ApplicationError("SCAN_UNAVAILABLE", type="ScanUnavailable")
        if fakes.outcome == "failed":
            return MastersScanResult(
                scan_id=inp.scan_id, state=MastersScanState.FAILED, code=ErrorCode.MALWARE
            )
        return MastersScanResult(
            scan_id=inp.scan_id,
            state=MastersScanState.DONE,
            document_id=uuid.uuid4() if fakes.parse_pending else None,
            parse_pending=fakes.parse_pending,
        )

    @activity.defn(name="fail_masters_scan")
    async def fail_masters_scan(inp: MastersScanInput) -> MastersScanResult:
        fakes.gave_up += 1
        return MastersScanResult(
            scan_id=inp.scan_id, state=MastersScanState.FAILED, code=ErrorCode.SCAN_UNAVAILABLE
        )

    @activity.defn(name="update_run_mirror")
    async def update_run_mirror(update: MirrorUpdate) -> None:
        fakes.mirror.append((update.state, update.current_stage, update.last_error))

    @activity.defn(name="parse_master_document")
    async def parse_master_document(inp: StageRef) -> IdsResult:
        fakes.parsed.append(inp.ref_id)
        return IdsResult(ids=[inp.ref_id])

    return [scan_and_import_masters, fail_masters_scan, update_run_mirror, parse_master_document]


@asynccontextmanager
async def running(fakes: Fakes) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    async with env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[MastersScanImport, MasterDocumentIngestion],
            activities=activities(fakes),
        ):
            yield env


async def start(env: WorkflowEnvironment, inp: MastersScanInput) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        MASTERS_SCAN_IMPORT,
        inp,
        id=masters_scan_workflow_id(inp.scan_id),
        task_queue=QUEUE,
        result_type=MastersScanResult,
    )


async def run_once(
    fakes: Fakes, kind: MastersScanKind = MastersScanKind.IMPORT
) -> MastersScanResult:
    async with running(fakes) as env:
        handle = await start(env, scan_input(kind))
        result: MastersScanResult = await handle.result()
        return result


def test_n02b_w01_a_scan_that_finishes_completes_the_run() -> None:
    async def scenario() -> None:
        fakes = Fakes()
        result = await run_once(fakes)
        assert result.state is MastersScanState.DONE and fakes.scans == 1 and fakes.gave_up == 0
        assert fakes.mirror == [
            (RunState.RUNNING, "scanning", None),
            (RunState.COMPLETED, "done", None),
        ]

    asyncio.run(scenario())


def test_n02b_w02_a_refused_file_fails_the_run_with_its_code_and_is_not_retried() -> None:
    async def scenario() -> None:
        fakes = Fakes(outcome="failed")
        result = await run_once(fakes)
        assert result.state is MastersScanState.FAILED and result.code is ErrorCode.MALWARE
        assert fakes.scans == 1 and fakes.gave_up == 0
        assert fakes.mirror[-1] == (RunState.FAILED, "failed", "MALWARE")

    asyncio.run(scenario())


def test_n02b_w03_an_unavailable_scanner_is_retried_then_failed() -> None:
    async def scenario() -> None:
        fakes = Fakes(unavailable_for=99)
        result = await run_once(fakes)
        assert fakes.scans == 5 and fakes.gave_up == 1  # the retry policy, then the give-up step
        assert result.state is MastersScanState.FAILED and result.code is ErrorCode.SCAN_UNAVAILABLE
        assert fakes.mirror[-1] == (RunState.FAILED, "failed", "SCAN_UNAVAILABLE")

    asyncio.run(scenario())


def test_n02b_w03_a_scanner_that_comes_back_ends_done() -> None:
    async def scenario() -> None:
        fakes = Fakes(unavailable_for=2)
        result = await run_once(fakes)
        assert result.state is MastersScanState.DONE and fakes.scans == 3 and fakes.gave_up == 0

    asyncio.run(scenario())


def test_n02b_w04_a_clean_document_starts_its_parse() -> None:
    async def scenario() -> None:
        fakes = Fakes(parse_pending=True)
        async with running(fakes) as env:
            handle = await start(env, scan_input(MastersScanKind.DOCUMENT))
            result: MastersScanResult = await handle.result()
            assert result.document_id is not None
            ingestion = env.client.get_workflow_handle(f"master-doc-{result.document_id}")
            await ingestion.result()
        assert fakes.parsed == [result.document_id]

    asyncio.run(scenario())


def test_n02b_w04_an_import_never_starts_a_parse() -> None:
    async def scenario() -> None:
        fakes = Fakes(parse_pending=True)
        await run_once(fakes, MastersScanKind.IMPORT)
        assert fakes.parsed == []

    asyncio.run(scenario())


def test_n02b_w05_the_scan_is_no_stage_of_the_project_pipeline() -> None:
    assert "scan_and_import_masters" not in {s.value for s in PipelineStage}


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_masters_scan_import_history() -> None:
    async def scenario() -> None:
        async with running(Fakes(parse_pending=True)) as env:
            handle = await start(env, scan_input(MastersScanKind.DOCUMENT))
            await handle.result()
            history = await handle.fetch_history()
            data: dict[str, Any] = json.loads(history.to_json())
        HISTORY.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")

    asyncio.run(scenario())
