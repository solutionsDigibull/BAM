#!/usr/bin/env python3
"""sbam-llm-mock: deterministic OpenAI-compatible upstream behind Bifrost (E1.15, TD §5.4, stdlib only).

CI and local runs use it instead of OpenRouter (`LLM_PROVIDER=mock`). Bifrost routes an alias such as
`sbam-sow` to the model `sbam-sow-primary` (or `sbam-sow-fallback`), so the alias is the model id minus that
suffix. A reply is looked up by alias + `metadata.prompt_id` (default `default`) + sha256 of the messages in
fixtures `<LLM_MOCK_FIXTURES>/<alias>/*.json`. There is no pass-through and no guessing: an unseen input is
a `MOCK_FIXTURE_MISSING` error that names the hash to record. The same input always gives the same bytes.

Scenarios, set with `PUT /__mock/scenario {"alias": "sbam-sow" | "*", "scenario": ...}` or per call with
`metadata.mock_scenario`:
    ok              the fixture (default)
    schema_invalid  the fixture's `invalid_response`, else truncated JSON that no schema accepts. Every answer is
                    invalid; `schema_invalid:2` is invalid for the first two calls of a prompt and valid after
                    that (the re-ask loop's "invalid, invalid, valid"). A re-ask appends an assistant and a user
                    turn, so the mock counts assistant turns and finds the fixture by the original prompt.
    timeout         sleep LLM_MOCK_TIMEOUT_S (default 65, longer than Bifrost's 30 s), then 504
    5xx             503 from the primary model only, so Bifrost falls back to the fallback model
    5xx_all         503 from both models
"""

import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCENARIOS = ("ok", "schema_invalid", "timeout", "5xx", "5xx_all")
FORBIDDEN_ENVS = ("staging", "production", "prod")
DEFAULT_PROMPT_ID = "default"
PORT = 8091
HOST = "0.0.0.0"  # noqa: S104 - container port, internal network only
INVALID_BODY = '{"mock":"schema_invalid"'  # truncated JSON, never schema-valid
CREATED = 1_700_000_000  # fixed so replies are byte-identical


def check_env(env: str, provider: str) -> None:
    """Refuse to run the mock in staging or production (E1.15-T09)."""
    if provider.lower() == "mock" and env.lower() in FORBIDDEN_ENVS:
        raise SystemExit(f"LLM_PROVIDER=mock is forbidden when ENV={env}")


def input_hash(messages: Any) -> str:
    canonical = json.dumps(
        messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_fixtures(root: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    fixtures: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted(root.glob("*/*.json")):
        data = json.loads(path.read_text())
        key = (path.parent.name, data["prompt_id"], data["input_hash"])
        if key in fixtures:
            raise ValueError(f"duplicate fixture for {key}: {path}")
        fixtures[key] = data
    return fixtures


def alias_of(model: str, aliases: set[str]) -> tuple[str, str] | None:
    """`sbam-sow-primary` -> (`sbam-sow`, `primary`); None for any other model id."""
    for role in ("primary", "fallback"):
        suffix = f"-{role}"
        if model.endswith(suffix) and model[: -len(suffix)] in aliases:
            return model[: -len(suffix)], role
    return None


def parse_scenario(scenario: str) -> tuple[str, int | None] | None:
    """`ok` -> ("ok", None); `schema_invalid:2` -> ("schema_invalid", 2); anything unknown -> None."""
    name, _, count = scenario.partition(":")
    if name not in SCENARIOS:
        return None
    if not count:
        return name, None
    if name != "schema_invalid" or not count.isdigit():
        return None
    return name, int(count)


def reasks_in(messages: list[Any]) -> int:
    """Re-asks already made: each one appends an assistant turn and a user turn to a single-shot prompt."""
    return sum(
        1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
    )


def original_prompt(messages: list[Any]) -> list[Any]:
    """The prompt before any re-ask, so a re-ask is served by the same fixture as the first call."""
    return messages[: max(len(messages) - 2 * reasks_in(messages), 1)]


def error(status: int, code: str, message: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": "mock_error", "code": code}}


class Mock:
    def __init__(
        self, fixtures: dict[tuple[str, str, str], dict[str, Any]], aliases: set[str]
    ):
        self.fixtures = fixtures
        self.aliases = aliases
        self.scenarios: dict[str, str] = {}
        self.calls: list[dict[str, str]] = []
        self.timeout_s = float(os.environ.get("LLM_MOCK_TIMEOUT_S", "65"))
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.scenarios.clear()
            self.calls.clear()

    def set_scenario(self, alias: str, scenario: str) -> None:
        if parse_scenario(scenario) is None or (
            alias != "*" and alias not in self.aliases
        ):
            raise ValueError(f"bad alias/scenario: {alias!r}/{scenario!r}")
        with self._lock:
            self.scenarios[alias] = scenario

    def chat(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        model = str(body.get("model", ""))
        found = alias_of(model, self.aliases)
        if found is None:
            return error(
                404, "MOCK_UNKNOWN_MODEL", f"model {model!r} is not an sbam alias model"
            )
        alias, role = found
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return error(400, "MOCK_BAD_REQUEST", "messages must be a non-empty list")
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            return error(400, "MOCK_BAD_REQUEST", "metadata must be an object")
        prompt_id = str(metadata.get("prompt_id", DEFAULT_PROMPT_ID))
        reasks = reasks_in(messages)
        digest = input_hash(original_prompt(messages))
        with self._lock:
            scenario = str(
                metadata.get("mock_scenario")
                or self.scenarios.get(alias)
                or self.scenarios.get("*")
                or "ok"
            )
            self.calls.append(
                {
                    "alias": alias,
                    "role": role,
                    "model": model,
                    "prompt_id": prompt_id,
                    "hash": digest,
                }
            )
        parsed = parse_scenario(scenario)
        if parsed is None:
            return error(400, "MOCK_BAD_SCENARIO", f"unknown scenario {scenario!r}")
        scenario, invalid_times = parsed
        if scenario == "5xx_all" or (scenario == "5xx" and role == "primary"):
            return error(503, "MOCK_UPSTREAM_DOWN", f"mock {scenario} for {model}")
        if scenario == "timeout":
            time.sleep(self.timeout_s)
            return error(504, "MOCK_TIMEOUT", f"mock timeout for {model}")
        fixture = self.fixtures.get((alias, prompt_id, digest))
        if fixture is None:
            return error(
                422,
                "MOCK_FIXTURE_MISSING",
                f"no fixture for alias={alias} prompt_id={prompt_id} input_hash={digest}",
            )
        invalid = scenario == "schema_invalid" and (
            invalid_times is None or reasks < invalid_times
        )
        content = fixture_content(fixture, invalid)
        completion = {
            "id": f"chatcmpl-mock-{digest[:16]}",
            "object": "chat.completion",
            "created": CREATED,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(json.dumps(messages)) // 4,
                "completion_tokens": len(content) // 4,
                "total_tokens": (len(json.dumps(messages)) + len(content)) // 4,
            },
        }
        return 200, completion


def fixture_content(fixture: dict[str, Any], force_invalid: bool) -> str:
    if force_invalid:
        invalid = fixture.get("invalid_response")
        return INVALID_BODY if invalid is None else dump(invalid)
    if "response_text" in fixture:
        return str(fixture["response_text"])
    return dump(fixture["response"])


def dump(value: Any) -> str:
    # No sort_keys: key order can be part of a contract (SowDocument sections), and the fixture file already
    # fixes the order, so the output stays byte-identical.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def make_handler(mock: Mock) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "sbam-llm-mock"
        sys_version = ""

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            parsed = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(parsed, dict):
                raise ValueError("body must be a JSON object")
            return parsed

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            elif self.path == "/v1/models":
                ids = [
                    f"{a}-{r}"
                    for a in sorted(mock.aliases)
                    for r in ("primary", "fallback")
                ]
                self._send(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": i, "object": "model"} for i in ids],
                    },
                )
            elif self.path == "/v1/auth/key":
                self._send(200, {"data": {"label": "mock"}})
            elif self.path == "/__mock/state":
                self._send(200, {"scenarios": mock.scenarios, "calls": mock.calls})
            else:
                self._send(*error(404, "NOT_FOUND", "not found"))

        def do_DELETE(self) -> None:
            if self.path == "/__mock/state":
                mock.reset()
                self._send(200, {"status": "reset"})
            else:
                self._send(*error(404, "NOT_FOUND", "not found"))

        def do_PUT(self) -> None:
            if self.path != "/__mock/scenario":
                self._send(*error(404, "NOT_FOUND", "not found"))
                return
            try:
                body = self._json_body()
                mock.set_scenario(
                    str(body.get("alias", "*")), str(body.get("scenario", ""))
                )
            except ValueError as exc:
                self._send(*error(400, "MOCK_BAD_REQUEST", str(exc)))
                return
            self._send(200, {"status": "ok", "scenarios": mock.scenarios})

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._send(*error(404, "NOT_FOUND", "not found"))
                return
            try:
                body = self._json_body()
            except ValueError as exc:
                self._send(*error(400, "MOCK_BAD_REQUEST", str(exc)))
                return
            self._send(*mock.chat(body))

        def log_message(self, fmt: str, *args: object) -> None:
            line = {"level": "INFO", "logger": "sbam_llm_mock", "msg": fmt % args}
            print(json.dumps(line), flush=True)

    return Handler


def build(fixtures_dir: Path, aliases_file: Path) -> Mock:
    aliases = set(json.loads(aliases_file.read_text()))
    return Mock(load_fixtures(fixtures_dir), aliases)


def main() -> None:
    check_env(
        os.environ.get("ENV", "development"), os.environ.get("LLM_PROVIDER", "mock")
    )
    mock = build(
        Path(os.environ.get("LLM_MOCK_FIXTURES", "/fixtures")),
        Path(os.environ.get("LLM_MOCK_ALIASES", "/app/aliases.json")),
    )
    print(
        json.dumps({"level": "INFO", "msg": f"{len(mock.fixtures)} fixtures loaded"}),
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), make_handler(mock)).serve_forever()


if __name__ == "__main__":
    main()
