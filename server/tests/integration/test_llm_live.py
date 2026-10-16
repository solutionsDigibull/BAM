"""E6.1 against the real models: the wrapper through Bifrost to OpenRouter, and Langfuse (opt-in).

Costs a few cents per run, so it only runs with `LIVE_LLM=1`. Needs Bifrost started with
`LLM_PROVIDER=bifrost` and `OPENROUTER_API_KEY` in compose/.env. Langfuse is checked when
LANGFUSE_HOST (or LANGFUSE_BASE_URL), LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set in the
shell:

    LIVE_LLM=1 uv run pytest -m integration tests/integration/test_llm_live.py -s
"""

import asyncio
import os
import time
from typing import Any

import httpx
import pytest
from llm_db import SmokeAnswer, llm_sessions, rows, scope
from pydantic import AnyHttpUrl
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings

from .conftest import is_open, port

pytestmark = pytest.mark.integration

BIFROST = f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}"
REQUEST = {"rfq": "Please quote 12 cables, delivery in March."}
LANGFUSE: dict[str, Any] = {
    "langfuse_host": os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL"),
    "langfuse_public_key": os.environ.get("LANGFUSE_PUBLIC_KEY"),
    "langfuse_secret_key": os.environ.get("LANGFUSE_SECRET_KEY"),
}
HAS_LANGFUSE = all(LANGFUSE.values())


@pytest.fixture(autouse=True)
def _live() -> None:
    if os.environ.get("LIVE_LLM") != "1":
        pytest.skip("live model calls cost money: set LIVE_LLM=1")
    if not is_open(port("BIFROST_PORT", 8081)):
        pytest.skip("bifrost is not published (start it with LLM_PROVIDER=bifrost)")


def make() -> tuple[LlmClient, Any]:
    sessions = llm_sessions()
    conf = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        temporal_host="temporal:7233",
        database_url="sqlite://",
        bifrost_url=AnyHttpUrl(BIFROST),
        llm_timeout_seconds=90,
        langfuse_log_content=False,
        **({k: v for k, v in LANGFUSE.items() if v} if HAS_LANGFUSE else {}),
    )
    client = LlmClient(
        conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient()
    )
    return client, sessions


@pytest.mark.parametrize(
    ("alias", "family"),
    [("sbam-sow", "gpt-4o"), ("sbam-testgen", "gpt-4o-mini")],
)
def test_e6_1_live_alias_answers_with_a_typed_result_and_a_provenance_row(
    alias: str, family: str
) -> None:
    client, sessions = make()
    sc = scope()
    result = asyncio.run(client.run(alias, "live-smoke", SmokeAnswer, REQUEST, sc))
    assert result.output.count == 12 and "cable" in result.output.label.lower()
    [row] = [r for r in rows(sessions, sc) if r.status == "SUCCEEDED"]
    print(
        f"\n{alias}: model={row.model} in={row.tokens_in} out={row.tokens_out} "
        f"cost={row.cost_usd} latency={row.latency_ms}ms trace={row.trace_id}"
    )
    assert row.model.removeprefix("openai/").startswith(family)
    assert row.tokens_in > 0 and row.tokens_out > 0 and row.latency_ms > 0
    assert row.trace_id == result.trace_id


@pytest.mark.skipif(not HAS_LANGFUSE, reason="set LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY")
def test_e6_1_live_trace_reaches_langfuse_and_resolves_by_trace_id() -> None:
    client, _ = make()
    result = asyncio.run(client.run("sbam-sow", "live-smoke", SmokeAnswer, REQUEST, scope()))
    assert client.tracer.failures == 0
    url = f"{LANGFUSE['langfuse_host'].rstrip('/')}/api/public/traces/{result.trace_id}"
    auth = (LANGFUSE["langfuse_public_key"] or "", LANGFUSE["langfuse_secret_key"] or "")
    for _ in range(15):  # Langfuse ingests asynchronously
        response = httpx.get(url, auth=auth, timeout=10)
        if response.status_code == 200:
            break
        time.sleep(2)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == result.trace_id
    assert any(o.get("model") for o in body.get("observations", []))
    assert "Please quote 12 cables" not in response.text  # content is redacted by default
