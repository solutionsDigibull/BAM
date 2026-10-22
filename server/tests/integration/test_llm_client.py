"""E6.1 against the Compose stack: the wrapper through Bifrost and the mock provider (TD §5.4).

Needs `make up` with LLM_PROVIDER=mock (the default). Rows go to SQLite (the Postgres table and its
migration are checked by the migration tests); what is real here is the HTTP path: alias routing in
Bifrost, the recorded answer in the mock, failover and the unreachable cases. Scenarios are set per
call with `metadata.mock_scenario`, which Bifrost forwards to the upstream.
"""

import asyncio
from typing import Any

import httpx
import pytest
from llm_db import SMOKE_CONTEXT, SmokeAnswer, llm_sessions, rows, scope
from pydantic import AnyHttpUrl
from sbam_workers.llm.client import LlmClient, LlmTransportError
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings

from .conftest import is_open, port

pytestmark = pytest.mark.integration

BIFROST = f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}"
CLOSED = "http://127.0.0.1:9"  # nothing listens here


@pytest.fixture(autouse=True)
def _bifrost_up() -> None:
    if not is_open(port("BIFROST_PORT", 8081)):
        pytest.skip("bifrost is not published (make up)")


def make(bifrost_url: str = BIFROST, **over: Any) -> tuple[LlmClient, Any]:
    sessions = llm_sessions()
    conf = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        temporal_host="temporal:7233",
        database_url="sqlite://",
        bifrost_url=AnyHttpUrl(bifrost_url),
        llm_backoff_seconds=0,
        **over,
    )
    client = LlmClient(
        conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient()
    )
    return client, sessions


def run(client: LlmClient, sc: Any, **metadata: str) -> Any:
    return asyncio.run(
        client.run("sbam-sow", "smoke", SmokeAnswer, SMOKE_CONTEXT, sc, metadata=metadata)
    )


def test_e6_1_t01_round_trip_through_bifrost_and_the_mock() -> None:
    client, sessions = make()
    sc = scope()
    result = run(client, sc)
    assert result.output == SmokeAnswer(count=3, label="ok")
    [row] = rows(sessions, sc)
    assert row.status == "SUCCEEDED" and row.model == "sbam-sow-primary"
    assert row.trace_id == result.trace_id


def test_e6_1_t02_bounded_reask_through_the_mock_schema_invalid_scenario() -> None:
    client, sessions = make()
    sc = scope()
    result = run(client, sc, mock_scenario="schema_invalid:2")
    assert result.attempts == 3
    assert [r.status for r in rows(sessions, sc)] == [
        "SCHEMA_INVALID",
        "SCHEMA_INVALID",
        "SUCCEEDED",
    ]


def test_e6_1_t03_always_invalid_ends_in_schema_invalid_after_retries() -> None:
    client, sessions = make()
    sc = scope()
    with pytest.raises(SchemaInvalidAfterRetries):
        run(client, sc, mock_scenario="schema_invalid")
    assert [r.status for r in rows(sessions, sc)] == ["SCHEMA_INVALID"] * 4


def test_e6_1_failover_to_the_fallback_model_is_recorded() -> None:
    client, sessions = make()
    sc = scope()
    run(client, sc, mock_scenario="5xx")
    assert rows(sessions, sc)[0].model == "sbam-sow-fallback"


def test_e6_1_t05_bifrost_down_is_a_retryable_transport_error_with_failed_rows() -> None:
    client, sessions = make(bifrost_url=CLOSED)
    sc = scope()
    with pytest.raises(LlmTransportError) as caught:
        run(client, sc)
    assert caught.value.retryable
    assert [r.status for r in rows(sessions, sc)] == ["FAILED"] * 3


def test_e6_1_both_models_failing_is_a_retryable_transport_error() -> None:
    client, sessions = make()
    sc = scope()
    with pytest.raises(LlmTransportError):
        run(client, sc, mock_scenario="5xx_all")
    assert {r.status for r in rows(sessions, sc)} == {"FAILED"}


def test_e6_1_t06_langfuse_down_the_call_still_succeeds_and_the_row_is_written() -> None:
    client, sessions = make(
        langfuse_host=CLOSED,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",  # noqa: S106
    )
    sc = scope()
    result = run(client, sc)
    assert result.output.count == 3
    assert [r.status for r in rows(sessions, sc)] == ["SUCCEEDED"]
    assert client.tracer.failures == 1
