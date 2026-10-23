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

Cognee's aliases (E5.7, #259) are marked in `aliases.json` and need no recorded fixtures:
    "mock": "generated"   (`sbam-knowledge`) an unseen input is answered with JSON built from the JSON
                          schema the caller sends (`response_format`, or the ```json block LiteLLM puts in
                          the system prompt). A knowledge-graph schema (`nodes` + `edges`) gets the input's
                          entity-like words as nodes, linked in order. No schema: a short plain answer.
    "kind": "embedding"   (`sbam-embed`) served on `/v1/embeddings` only: one unit vector per input,
                          hashed from its words, so the same text always gives the same vector and texts
                          that share words are close. Size: `dimensions` in the request, else
                          LLM_MOCK_EMBEDDING_DIMENSIONS (default 1536).
    "mock": "ocr"         (`sbam-ocr`, E4.8) an unseen input is answered with a page of text for the
                          page the request names (`<data name="page_number">N</data>`): the mock cannot
                          read an image, so the text is always `Mock OCR text of page N.`
The scenarios apply to all of them.
"""

import hashlib
import itertools
import json
import math
import os
import re
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
EMBEDDING_DIMENSIONS = int(os.environ.get("LLM_MOCK_EMBEDDING_DIMENSIONS", "1536"))
MAX_ENTITIES = 8
SCHEMA_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
ENTITY = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\b")
WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PLAIN_ANSWER = "test"  # Cognee's LLM connection test asks for exactly this


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
        self,
        fixtures: dict[tuple[str, str, str], dict[str, Any]],
        aliases: set[str],
        generated: frozenset[str] = frozenset(),
        embedding: frozenset[str] = frozenset(),
        ocr: frozenset[str] = frozenset(),
    ):
        self.fixtures = fixtures
        self.aliases = aliases
        self.generated = generated  # unseen inputs are answered from the request's schema
        self.embedding = embedding  # served on /v1/embeddings only
        self.ocr = ocr  # unseen inputs get a page of mock text for the page asked for
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
        if alias in self.embedding:
            return error(
                400, "MOCK_BAD_REQUEST", f"{alias} is an embedding alias: /v1/embeddings"
            )
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
        if fixture is None and alias not in self.generated and alias not in self.ocr:
            return error(
                422,
                "MOCK_FIXTURE_MISSING",
                f"no fixture for alias={alias} prompt_id={prompt_id} input_hash={digest}",
            )
        invalid = scenario == "schema_invalid" and (
            invalid_times is None or reasks < invalid_times
        )
        if fixture is None:
            if invalid:
                content = INVALID_BODY
            elif alias in self.ocr:
                content = ocr_answer(messages)
            else:
                content = generated_answer(body)
        else:
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

    def embeddings(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        model = str(body.get("model", ""))
        found = alias_of(model, self.aliases)
        if found is None or found[0] not in self.embedding:
            return error(
                404, "MOCK_UNKNOWN_MODEL", f"model {model!r} is not an embedding alias"
            )
        alias, role = found
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            return error(400, "MOCK_BAD_REQUEST", "input must be a string or strings")
        size = body.get("dimensions")
        size = EMBEDDING_DIMENSIONS if size is None else size
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 8192:
            return error(400, "MOCK_BAD_REQUEST", "dimensions must be 1..8192")
        with self._lock:
            scenario = str(
                self.scenarios.get(alias) or self.scenarios.get("*") or "ok"
            )
            self.calls.append(
                {"alias": alias, "role": role, "model": model, "prompt_id": "embed"}
            )
        if scenario == "5xx_all" or (scenario == "5xx" and role == "primary"):
            return error(503, "MOCK_UPSTREAM_DOWN", f"mock {scenario} for {model}")
        if scenario == "timeout":
            time.sleep(self.timeout_s)
            return error(504, "MOCK_TIMEOUT", f"mock timeout for {model}")
        data = [
            {"object": "embedding", "index": i, "embedding": embed(text, size)}
            for i, text in enumerate(texts)
        ]
        tokens = sum(len(t) for t in texts) // 4
        return 200, {
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }


def embed(text: str, size: int) -> list[float]:
    """Feature hashing of the words: deterministic, and shared words mean close vectors."""
    vector = [0.0] * size
    for word in WORD.findall(text.lower()) or [""]:
        digest = hashlib.sha256(word.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % size
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 6) for v in vector]


PAGE_NUMBER = re.compile(r'<data name="page_number">\s*(\d+)\s*</data>')


def message_text(message: dict[str, Any]) -> str:
    """The text of a message; the images of a vision message are not text and are left out."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def ocr_answer(messages: list[Any]) -> str:
    """The answer of `sbam-ocr` for the page the request names (page 1 when it names none)."""
    text = " ".join(message_text(m) for m in messages if isinstance(m, dict) and m.get("role") == "user")
    found = PAGE_NUMBER.search(text)
    page = int(found.group(1)) if found else 1
    return dump({"page": page, "markdown": f"Mock OCR text of page {page}.", "unreadable": False})


def generated_answer(body: dict[str, Any]) -> str:
    """A schema-valid answer for an unseen input (Cognee's `sbam-knowledge`)."""
    messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    schema = request_schema(body, messages)
    if schema is None:
        return PLAIN_ANSWER
    text = " ".join(
        str(m.get("content", "")) for m in messages if m.get("role") == "user"
    )
    return dump(instance(schema, schema, entities(text)))


def request_schema(body: dict[str, Any], messages: list[Any]) -> dict[str, Any] | None:
    fmt = body.get("response_format") or {}
    if isinstance(fmt, dict) and isinstance(fmt.get("json_schema"), dict):
        schema = fmt["json_schema"].get("schema")
        if isinstance(schema, dict):
            return schema
    for message in messages:
        for block in SCHEMA_BLOCK.findall(str(message.get("content", ""))):
            try:
                parsed = json.loads(block)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def entities(text: str) -> list[str]:
    """Up to MAX_ENTITIES distinct capitalised words or codes (`Mouser`, `ZEBRA-7731`)."""
    found = list(dict.fromkeys(ENTITY.findall(text)))
    return found[:MAX_ENTITIES] or ["Document"]


def instance(schema: Any, root: dict[str, Any], names: list[str], depth: int = 0) -> Any:
    """A minimal value valid for `schema`; a `nodes` + `edges` object becomes a graph."""
    if not isinstance(schema, dict) or depth > 8:
        return None
    if "$ref" in schema:
        return instance(resolve(schema, root), root, names, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        options = [o for o in schema.get(key) or [] if o.get("type") != "null"]
        if options:
            return instance(options[0], root, names, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    props = schema.get("properties") or {}
    if kind == "object" or props:
        if {"nodes", "edges"} <= set(props):
            return graph(props, root, names, depth)
        return {
            name: instance(props[name], root, names, depth + 1)
            for name in schema.get("required", list(props))
            if name in props
        }
    if kind == "array":
        items = [instance(schema.get("items", {}), root, names, depth + 1)]
        return items * max(1, int(schema.get("minItems", 1)))
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return "mock " + names[0]


def graph(
    props: dict[str, Any], root: dict[str, Any], names: list[str], depth: int
) -> dict[str, Any]:
    """Cognee's KnowledgeGraph: one node per entity, edges linking them in order."""
    node_schema = resolve(resolve(props["nodes"], root).get("items", {}), root)
    edge_schema = resolve(resolve(props["edges"], root).get("items", {}), root)
    nodes = [_fill(node_schema, root, names, depth, name, None) for name in names]
    pairs = itertools.pairwise(names)
    edges = [_fill(edge_schema, root, names, depth, a, b) for a, b in pairs]
    return {"nodes": nodes, "edges": edges}


def resolve(schema: Any, root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local `$ref` (`#/$defs/Node`); anything else is returned as is."""
    ref = schema.get("$ref", "") if isinstance(schema, dict) else ""
    if not ref.startswith("#/"):
        return schema if isinstance(schema, dict) else {}
    target: Any = root
    for part in ref[2:].split("/"):
        target = target.get(part, {}) if isinstance(target, dict) else {}
    return target if isinstance(target, dict) else {}


def _fill(
    schema: dict[str, Any],
    root: dict[str, Any],
    names: list[str],
    depth: int,
    name: str,
    target: str | None,
) -> dict[str, Any]:
    """A node (`target` None) or an edge from `name` to `target`, field by field."""
    props = schema.get("properties") or {}
    out: dict[str, Any] = {}
    for field in schema.get("required", list(props)):
        lower = field.lower()
        if target is not None and "source" in lower:
            out[field] = name
        elif target is not None and "target" in lower:
            out[field] = target
        elif target is not None and ("relation" in lower or "name" in lower):
            out[field] = "related_to"
        elif lower in ("id", "name"):
            out[field] = name
        elif lower == "type":
            out[field] = "Entity"
        elif lower == "description":
            out[field] = f"{name}, mentioned in the document"
        else:
            out[field] = instance(props.get(field, {}), root, names, depth + 1)
    return out


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
            routes = {"/v1/chat/completions": mock.chat, "/v1/embeddings": mock.embeddings}
            if self.path not in routes:
                self._send(*error(404, "NOT_FOUND", "not found"))
                return
            try:
                body = self._json_body()
            except ValueError as exc:
                self._send(*error(400, "MOCK_BAD_REQUEST", str(exc)))
                return
            self._send(*routes[self.path](body))

        def log_message(self, fmt: str, *args: object) -> None:
            line = {"level": "INFO", "logger": "sbam_llm_mock", "msg": fmt % args}
            print(json.dumps(line), flush=True)

    return Handler


def build(fixtures_dir: Path, aliases_file: Path) -> Mock:
    specs: dict[str, dict[str, str]] = json.loads(aliases_file.read_text())
    return Mock(
        load_fixtures(fixtures_dir),
        set(specs),
        generated=frozenset(a for a, s in specs.items() if s.get("mock") == "generated"),
        embedding=frozenset(a for a, s in specs.items() if s.get("kind") == "embedding"),
        ocr=frozenset(a for a, s in specs.items() if s.get("mock") == "ocr"),
    )


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
