"""Bounded schema re-ask loop (E1.15-T05, shared with E6.1-T02/T03): fakes plus the mock's logic."""

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from sbam_contracts.sow import SowDocument
from sbam_workers.llm.reask import (
    MAX_REASKS,
    Attempt,
    Message,
    Reply,
    SchemaInvalidAfterRetries,
    run_structured,
    strip_fences,
)

REPO = Path(__file__).resolve().parents[3]
SMOKE: list[Message] = [{"role": "user", "content": "sbam-mock-smoke"}]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    label: str


def load_mock_module() -> Any:
    path = REPO / "compose/docker/llm-mock/server.py"
    spec = importlib.util.spec_from_file_location("sbam_llm_mock_reask", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


srv = load_mock_module()


def scripted(*texts: str, model: str = "m") -> tuple[Callable[[list[Message]], Any], list[Any]]:
    """A fake LLM that answers `texts` in order and records every request."""
    seen: list[Any] = []

    async def call(messages: list[Message]) -> Reply:
        seen.append(messages)
        return Reply(texts[len(seen) - 1], model)

    return call, seen


def mock_call(mock: Any, scenario: str, alias: str = "sbam-sow") -> Callable[..., Any]:
    async def call(messages: list[Message]) -> Reply:
        status, body = mock.chat(
            {
                "model": f"{alias}-primary",
                "messages": messages,
                "metadata": {"prompt_id": "smoke", "mock_scenario": scenario},
            }
        )
        assert status == 200, body
        return Reply(body["choices"][0]["message"]["content"], body["model"])

    return call


@pytest.fixture
def mock() -> Any:
    return srv.build(REPO / "server/tests/llm-mock", REPO / "compose/bifrost/aliases.json")


GOOD = '{"count": 2, "label": "x"}'
BAD = '{"count": "two", "label": "x"}'


def test_e1_15_t05_schema_invalid_scenario_ends_in_schema_invalid_after_retries(
    mock: Any,
) -> None:
    with pytest.raises(SchemaInvalidAfterRetries) as caught:
        asyncio.run(run_structured(mock_call(mock, "schema_invalid"), SMOKE, SowDocument))
    assert [a.status for a in caught.value.attempts] == ["SCHEMA_INVALID"] * (MAX_REASKS + 1)
    assert len(mock.calls) == MAX_REASKS + 1  # one call + 3 re-asks, never a 5th
    assert caught.value.retryable is False
    assert {c["hash"] for c in mock.calls} == {
        mock.calls[0]["hash"]
    }  # a re-ask hits the same fixture


def test_e1_15_t05_two_invalid_answers_then_a_valid_sow_succeeds_on_attempt_3(
    mock: Any,
) -> None:
    result = asyncio.run(run_structured(mock_call(mock, "schema_invalid:2"), SMOKE, SowDocument))
    assert [a.status for a in result.attempts] == [
        "SCHEMA_INVALID",
        "SCHEMA_INVALID",
        "SUCCEEDED",
    ]
    assert result.output.modules  # the real, contract-valid sbam-sow fixture


def test_e1_15_valid_first_answer_makes_one_call() -> None:
    call, seen = scripted(GOOD)
    result = asyncio.run(run_structured(call, SMOKE, Answer))
    assert result.output == Answer(count=2, label="x")
    assert len(seen) == 1 and result.attempts == (Attempt(1, "SUCCEEDED", "m"),)


def test_e1_15_reask_carries_validation_errors_only_not_values_or_documents() -> None:
    leaky = '{"count": "TOP-SECRET-VALUE", "label": "x", "extra_field": 1}'
    call, seen = scripted(leaky, GOOD)
    asyncio.run(run_structured(call, SMOKE, Answer))
    first, second = seen
    assert first == SMOKE
    assert second[: len(SMOKE)] == SMOKE and len(second) == len(SMOKE) + 2
    feedback = second[-1]["content"]
    assert second[-2] == {"role": "assistant", "content": leaky}
    assert "count:" in feedback and "extra_field:" in feedback
    assert "TOP-SECRET-VALUE" not in feedback  # pydantic's `input` is never echoed


def test_e1_15_exhaustion_makes_exactly_max_reasks_plus_one_calls() -> None:
    call, seen = scripted(*[BAD] * 10)
    with pytest.raises(SchemaInvalidAfterRetries) as caught:
        asyncio.run(run_structured(call, SMOKE, Answer))
    assert len(seen) == 4 and len(caught.value.attempts) == 4
    assert "after 4 attempts" in str(caught.value)


def test_e1_15_max_reasks_zero_means_a_single_call() -> None:
    call, seen = scripted(BAD, GOOD)
    with pytest.raises(SchemaInvalidAfterRetries):
        asyncio.run(run_structured(call, SMOKE, Answer, max_reasks=0))
    assert len(seen) == 1


def test_e1_15_strict_validation_does_not_coerce() -> None:
    call, _ = scripted('{"count": "2", "label": "x"}', GOOD)
    result = asyncio.run(run_structured(call, SMOKE, Answer))
    assert result.attempts[0].status == "SCHEMA_INVALID" and result.output.count == 2


def test_e1_15_json_wrapped_in_markdown_fences_is_valid() -> None:
    call, seen = scripted(f"```json\n{GOOD}\n```")
    assert asyncio.run(run_structured(call, SMOKE, Answer)).output.count == 2
    assert len(seen) == 1
    assert strip_fences(f"```\n{GOOD}\n```") == GOOD
    assert strip_fences(GOOD) == GOOD


def test_e1_15_truncated_json_is_a_schema_error_not_a_crash() -> None:
    call, _ = scripted('{"count": 2, "label"', GOOD)
    result = asyncio.run(run_structured(call, SMOKE, Answer))
    assert [a.status for a in result.attempts] == ["SCHEMA_INVALID", "SUCCEEDED"]
    assert result.attempts[0].errors  # the caller's llm_calls row can store why


def test_e1_15_transport_errors_propagate_and_are_not_reasks() -> None:
    calls = 0

    async def call(messages: list[Message]) -> Reply:
        nonlocal calls
        calls += 1
        raise ConnectionError("bifrost down")

    with pytest.raises(ConnectionError):
        asyncio.run(run_structured(call, SMOKE, Answer))
    assert calls == 1


def test_e1_15_on_attempt_sees_every_attempt_including_failures_and_the_answering_model() -> None:
    rows: list[tuple[int, str, str]] = []

    async def record(attempt: Attempt, reply: Reply) -> None:
        rows.append((attempt.number, attempt.status, reply.model))

    call, _ = scripted(BAD, GOOD, model="fallback-model")
    asyncio.run(run_structured(call, SMOKE, Answer, on_attempt=record))
    assert rows == [
        (1, "SCHEMA_INVALID", "fallback-model"),
        (2, "SUCCEEDED", "fallback-model"),
    ]


def test_e1_15_mock_scenario_parsing() -> None:
    assert srv.parse_scenario("ok") == ("ok", None)
    assert srv.parse_scenario("schema_invalid:2") == ("schema_invalid", 2)
    for bad in ("nope", "ok:2", "schema_invalid:x", "timeout:1", "schema_invalid:-1"):
        assert srv.parse_scenario(bad) is None


def test_e1_15_mock_finds_the_original_prompt_after_reasks() -> None:
    first: list[Message] = [{"role": "user", "content": "q"}]
    after_two = [
        *first,
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "fix"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "fix"},
    ]
    assert srv.original_prompt(after_two) == first
    assert srv.original_prompt(first) == first
    assert json.dumps(srv.original_prompt([])) == "[]"
