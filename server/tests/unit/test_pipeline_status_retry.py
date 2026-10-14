"""E10.1: the status query is retried when it races a continue-as-new (transient RPC errors)."""

import asyncio
import uuid
from typing import Any, cast

import pytest
from sbam_api.pipeline import service
from sbam_api.pipeline.service import pipeline_status
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_contracts.pipeline import PipelineStage, PipelineStatus
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

STATUS = PipelineStatus(stage=PipelineStage.WAIT_SOW_APPROVAL, waiting_for="sow_approved")


class FlakyHandle:
    def __init__(self, failures: list[RPCStatusCode]) -> None:
        self.failures, self.calls = list(failures), 0

    async def query(self, name: str, **kwargs: Any) -> PipelineStatus:
        self.calls += 1
        assert name == "status" and kwargs["rpc_timeout"] is not None
        if self.failures:
            raise RPCError("transient", self.failures.pop(0), b"")
        return STATUS


class FakeClient:
    def __init__(self, handle: FlakyHandle) -> None:
        self.handle = handle

    def get_workflow_handle(self, workflow_id: str) -> FlakyHandle:
        assert workflow_id.startswith("pipeline-")
        return self.handle


def status_of(handle: FlakyHandle) -> PipelineStatus | None:
    return asyncio.run(pipeline_status(cast(Client, FakeClient(handle)), uuid.uuid4()))


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "QUERY_BACKOFF_SECONDS", 0.0)


@pytest.mark.parametrize(
    "first",
    [
        RPCStatusCode.CANCELLED,  # the client's own rpc_timeout expired
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.UNAVAILABLE,
        RPCStatusCode.NOT_FOUND,
    ],
)
def test_e10_1_status_query_racing_continue_as_new_is_retried(first: RPCStatusCode) -> None:
    handle = FlakyHandle([first])
    assert status_of(handle) == STATUS
    assert handle.calls == 2


def test_e10_1_never_started_is_none_after_retries() -> None:
    handle = FlakyHandle([RPCStatusCode.NOT_FOUND] * 10)
    assert status_of(handle) is None
    assert handle.calls == service.QUERY_ATTEMPTS


@pytest.mark.parametrize(
    "failures",
    [[RPCStatusCode.DEADLINE_EXCEEDED] * 10, [RPCStatusCode.PERMISSION_DENIED]],
    ids=["still-timing-out", "not-transient"],
)
def test_e10_1_persistent_or_other_errors_are_503(failures: list[RPCStatusCode]) -> None:
    with pytest.raises(OrchestratorUnavailable):
        status_of(FlakyHandle(failures))
