"""E1.15 against the Compose stack: the six sbam-* aliases through Bifrost and the mock (TD §5.4).

Needs `make up` with the default LLM_PROVIDER=mock. Scenarios are set per call with
`metadata.mock_scenario`, which Bifrost forwards to the upstream, so no admin port is published.
"""

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.sow import SowDocument
from sbam_workers.llm.reask import (
    MAX_REASKS,
    LlmCall,
    Message,
    Reply,
    SchemaInvalidAfterRetries,
    run_structured,
)

from .conftest import REPO, Compose, is_open, port

pytestmark = pytest.mark.integration

ALIASES = ["sbam-ocr", "sbam-sow", "sbam-isatvon", "sbam-codegen", "sbam-testgen", "sbam-corrector"]
FIXTURES = REPO / "server/tests/llm-mock"
SMOKE = [{"role": "user", "content": "sbam-mock-smoke"}]
BASE = f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}"


@pytest.fixture(autouse=True)
def _bifrost_up() -> None:
    if not is_open(port("BIFROST_PORT", 8081)):
        pytest.skip("bifrost is not published (make up)")


def call(body: dict[str, Any]) -> tuple[int, dict[str, str], dict[str, Any]]:
    req = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"{BASE}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read())


def fixture_body(alias: str, **metadata: str) -> dict[str, Any]:
    path = next(p for p in sorted((FIXTURES / alias).glob("*.json")) if p.stem != "e6_1_wrapper")
    data = json.loads(Path(path).read_text())
    meta = {"prompt_id": data["prompt_id"], **metadata}
    return {"model": alias, "messages": data["messages"], "metadata": meta}


@pytest.mark.parametrize("alias", ALIASES)
def test_e1_15_t01_each_alias_answers_through_bifrost_with_its_model_recorded(alias: str) -> None:
    status, headers, body = call(fixture_body(alias))
    assert status == 200
    assert headers["X-Bifrost-Resolved-Model"] == f"{alias}-primary"
    assert body["model"] == f"{alias}-primary"
    assert body["choices"][0]["message"]["content"]


@pytest.mark.parametrize("model", ["gpt-4o", "openrouter/openai/gpt-4o", "sbam-nope"])
def test_e1_15_t02_models_that_are_not_aliases_are_rejected(model: str) -> None:
    status, headers, body = call({"model": model, "messages": SMOKE})
    assert status == 400
    assert "X-Bifrost-Routing-Info-Model" not in headers  # never routed, so no upstream call
    assert "error" in body


def test_e1_15_t03_same_request_gives_identical_completion() -> None:
    def stable(body: dict[str, Any]) -> str:
        # Bifrost stamps its own measured timings (ms) into extra_fields; the rest is the mock's.
        extra = {k: v for k, v in body["extra_fields"].items() if "latency" not in k}
        return json.dumps({**body, "extra_fields": extra}, sort_keys=True)

    assert stable(call(fixture_body("sbam-sow"))[2]) == stable(call(fixture_body("sbam-sow"))[2])


def test_e1_15_t04_primary_503_is_answered_by_the_fallback_model() -> None:
    status, headers, body = call(fixture_body("sbam-sow", mock_scenario="5xx"))
    assert status == 200
    assert headers["X-Bifrost-Routing-Info-Is-Fallback"] == "true"
    assert headers["X-Bifrost-Resolved-Model"] == "sbam-sow-fallback"
    assert body["model"] == "sbam-sow-fallback"


def test_e1_15_t04_when_both_models_fail_the_caller_sees_the_error() -> None:
    status, _, body = call(fixture_body("sbam-sow", mock_scenario="5xx_all"))
    assert status == 503
    assert body["error"]["code"] == "MOCK_UPSTREAM_DOWN"


def test_e1_15_t05_schema_invalid_scenario_reaches_the_caller_unparseable() -> None:
    status, _, body = call(fixture_body("sbam-sow", mock_scenario="schema_invalid"))
    assert status == 200
    with pytest.raises(json.JSONDecodeError):
        json.loads(body["choices"][0]["message"]["content"])


def test_e1_15_t08_unseen_input_fails_with_mock_fixture_missing() -> None:
    body = fixture_body("sbam-sow")
    body["messages"] = [{"role": "user", "content": "never recorded"}]
    status, _, err = call(body)
    assert status == 422
    assert err["error"]["code"] == "MOCK_FIXTURE_MISSING"


def bifrost_call(scenario: str) -> LlmCall:
    """The re-ask loop's `LlmCall` over the real gateway: alias in, answering model out."""

    async def send(messages: list[Message]) -> Reply:
        body = {
            "model": "sbam-sow",
            "messages": messages,
            "metadata": {"prompt_id": "smoke", "mock_scenario": scenario},
        }
        status, _, data = await asyncio.to_thread(call, body)
        assert status == 200, data
        return Reply(data["choices"][0]["message"]["content"], data["model"])

    return send


def test_e1_15_t05_schema_invalid_through_bifrost_stops_after_three_reasks() -> None:
    with pytest.raises(SchemaInvalidAfterRetries) as caught:
        asyncio.run(run_structured(bifrost_call("schema_invalid"), SMOKE, SowDocument))
    assert len(caught.value.attempts) == MAX_REASKS + 1


def test_e1_15_t05_two_invalid_answers_then_valid_through_bifrost() -> None:
    result = asyncio.run(run_structured(bifrost_call("schema_invalid:2"), SMOKE, SowDocument))
    assert [a.status for a in result.attempts] == ["SCHEMA_INVALID", "SCHEMA_INVALID", "SUCCEEDED"]
    assert result.attempts[-1].model == "sbam-sow-primary"


@pytest.mark.parametrize("service", ["sbam-api", "worker-llm", "worker-knowledge"])
def test_e1_15_t06_openrouter_key_is_absent_from_api_and_workers(
    compose: Compose, service: str
) -> None:
    out = compose("exec", "-T", service, "printenv")
    if out.returncode != 0:
        pytest.skip(f"{service} is not running")
    assert "OPENROUTER" not in out.stdout


def test_e1_15_t07_mock_container_has_no_route_to_openrouter(compose: Compose) -> None:
    probe = (
        "import socket\n"
        "try: socket.create_connection(('openrouter.ai',443),timeout=5).close(); print('open')\n"
        "except OSError: print('blocked')"
    )
    out = compose("exec", "-T", "sbam-llm-mock", "python", "-c", probe)
    assert out.stdout.strip() == "blocked"
