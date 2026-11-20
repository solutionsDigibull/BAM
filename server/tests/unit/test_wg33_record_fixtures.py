# ruff: noqa: E501
"""WG-33 (#914): recording tooling for the mock fixtures, and the fixtures themselves.

The recording proxy (`scripts/llm_record_proxy.py`) writes what the workers and Bifrost exchanged as mock fixtures.
These tests need no network and no model: they feed the proxy's recorder a made-up exchange for every stage of the
build chain and check the mock then answers it, and they check the committed fixtures as a whole.
"""

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "server/tests/llm-mock"
ALIASES_FILE = REPO / "compose/bifrost/aliases.json"

# (alias, prompt_id) of every stage of the chain; ui-copy is called through sbam-codegen.
CHAIN = [
    ("sbam-sow", "sow-generate"),
    ("sbam-isatvon", "isatvon-macros"),
    ("sbam-isatvon", "isatvon-sub-skills"),
    ("sbam-codegen", "codegen-bodies"),
    ("sbam-testgen", "testgen"),
    ("sbam-corrector", "correct-code"),
    ("sbam-codegen", "ui-copy"),
    ("sbam-rubric", "rubric"),
]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


srv = load("llm_mock_server_wg33", REPO / "compose/docker/llm-mock/server.py")
rec = load("llm_record_proxy_wg33", REPO / "scripts/llm_record_proxy.py")


def exchange(alias: str, prompt_id: str, turns: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": f"system for {prompt_id}"},
        {
            "role": "user",
            "content": f'<data name="doc">\nsynthetic {alias} {prompt_id}\n</data>',
        },
    ]
    for _ in range(turns):  # a re-ask appends an assistant turn and a user turn
        messages += [
            {"role": "assistant", "content": "{bad"},
            {"role": "user", "content": "fix it"},
        ]
    body = {
        "model": alias,
        "messages": messages,
        "metadata": {"prompt_id": prompt_id, "tenant_id": "t-1"},
    }
    reply = {
        "model": "x/y",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '{"ok": true, "stage": "' + prompt_id + '"}',
                }
            }
        ],
        "usage": {"cost": 0.01},
    }
    return body, reply


def mock_on(directory: Path) -> Any:
    return srv.build(directory, ALIASES_FILE)


def test_wg33_t01_every_chain_stage_finds_its_fixture_on_the_mock(
    tmp_path: Path,
) -> None:
    recorder = rec.Recorder(tmp_path, 10.0)
    for alias, prompt_id in CHAIN:
        body, reply = exchange(alias, prompt_id)
        assert recorder.record(body, reply) is not None
    mock = mock_on(tmp_path)
    for alias, prompt_id in CHAIN:
        body, reply = exchange(alias, prompt_id)
        body["model"] = f"{alias}-primary"
        status, answer = mock.chat(body)
        assert status == 200, (alias, prompt_id, answer)
        assert (
            answer["choices"][0]["message"]["content"] == reply["choices"][0]["message"]["content"]
        )


def test_wg33_t01b_the_answer_of_the_deepest_reask_replaces_the_earlier_one(
    tmp_path: Path,
) -> None:
    recorder = rec.Recorder(tmp_path, 10.0)
    first_body, first_reply = exchange("sbam-sow", "sow-generate")
    again_body, again_reply = exchange("sbam-sow", "sow-generate", turns=1)
    again_reply["choices"][0]["message"]["content"] = '{"final": true}'
    assert recorder.record(first_body, first_reply) == recorder.record(again_body, again_reply)
    assert recorder.record(
        first_body, first_reply
    )  # a late shallow answer must not undo the deeper one
    mock = mock_on(tmp_path)
    first_body["model"] = "sbam-sow-primary"
    status, answer = mock.chat(first_body)
    assert status == 200 and answer["choices"][0]["message"]["content"] == '{"final": true}'


def test_wg33_t02_the_mock_still_refuses_an_unknown_prompt(tmp_path: Path) -> None:
    recorder = rec.Recorder(tmp_path, 10.0)
    body, reply = exchange("sbam-sow", "sow-generate")
    recorder.record(body, reply)
    other, _ = exchange("sbam-sow", "sow-generate")
    other["messages"][1]["content"] += " changed"
    other["model"] = "sbam-sow-primary"
    status, answer = mock_on(tmp_path).chat(other)
    assert status == 422 and answer["error"]["code"] == "MOCK_FIXTURE_MISSING"


def test_wg33_t03_the_proxy_hash_is_the_mocks_hash() -> None:
    messages = [
        {"role": "user", "content": "café ✓"},
        {"role": "system", "content": "b"},
    ]
    assert rec.input_hash(messages) == srv.input_hash(messages)
    assert rec.original_prompt(messages) == srv.original_prompt(messages)


def test_wg33_t04_recording_is_off_without_the_switch_and_a_cap() -> None:
    with pytest.raises(SystemExit):
        rec.check_opt_in({})
    with pytest.raises(SystemExit):
        rec.check_opt_in({"LLM_RECORD": "1"})
    with pytest.raises(SystemExit):
        rec.check_opt_in({"LLM_RECORD": "1", "LLM_RECORD_SPEND_CAP_USD": "0"})
    with pytest.raises(SystemExit):
        rec.check_opt_in({"LLM_RECORD": "1", "LLM_RECORD_SPEND_CAP_USD": "abc"})
    assert rec.check_opt_in({"LLM_RECORD": "1", "LLM_RECORD_SPEND_CAP_USD": "3.6"}) == 3.6


def test_wg33_t05_the_spend_cap_stops_further_calls(tmp_path: Path) -> None:
    recorder = rec.Recorder(tmp_path, 0.025)
    for _ in range(2):
        body, reply = exchange("sbam-sow", "sow-generate")
        recorder.record(body, reply)
    assert not recorder.over_cap()
    recorder.record(*exchange("sbam-testgen", "testgen"))
    assert recorder.over_cap()


def test_wg33_t06_a_fixture_holds_only_messages_and_the_answer(tmp_path: Path) -> None:
    body, reply = exchange("sbam-sow", "sow-generate")
    path = rec.Recorder(tmp_path, 1.0).record(body, reply)
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {
        "prompt_id",
        "input_hash",
        "messages",
        "valid",
        "response_text",
        "note",
    }
    assert "t-1" not in path.read_text(
        encoding="utf-8"
    )  # the request metadata (tenant, project) is never kept


# Patterns of key-like strings, written so that this file itself holds none.
KEY_LIKE = [
    re.compile(r"sk-(?:or-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}"
    ),
]


def test_wg33_t07_no_key_like_string_in_any_committed_fixture() -> None:
    files = sorted(FIXTURES.glob("*/*.json"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in KEY_LIKE:
            assert not pattern.search(text), f"{path.name} matches {pattern.pattern}"


def test_wg33_t08_every_committed_fixture_is_found_by_the_mock() -> None:
    mock = srv.build(FIXTURES, ALIASES_FILE)
    for (alias, prompt_id, digest), data in mock.fixtures.items():
        assert srv.input_hash(data["messages"]) == digest, f"{alias}/{prompt_id}: stale input_hash"
        body = {
            "model": f"{alias}-primary",
            "messages": data["messages"],
            "metadata": {"prompt_id": prompt_id},
        }
        assert mock.chat(body)[0] == 200, (alias, prompt_id)
