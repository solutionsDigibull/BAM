#!/usr/bin/env python3
"""sbam-mock-api (E13.5, TD §15.2): the answers a skill's tool gets from "the real API" in a test.

Standard library only, so the image stays a bare python:slim. Two surfaces on one port:

  Control, bearer token (the worker and the sandbox adapter hold it; a tool under test does not):
    PUT    /_control/runs/{run_id}          `MockRunSetup`: the stubs of one sandbox run
    DELETE /_control/runs/{run_id}          forget the run
    GET    /_control/runs/{run_id}/journal  `MockJournal`: every request the run made, in order
  Data, no credentials (anything a tool calls): `X-Sbam-Run-Id` picks the run's namespace and the
    optional `X-Sbam-Case` the case. Authorization and cookies are accepted and ignored.

Safety, all enforced here and not only by the callers:
  * Never forwards. This process opens no outbound connection and has no proxy, record or
    passthrough mode: a stub is a status and a JSON body, and a request nothing matches is a 404
    MOCK_UNMATCHED.
  * One namespace per run: stubs, call counters and the journal belong to a run id, so four parallel
    builds cannot read each other's sequences. A data request for a run that was never set up is a
    404 and creates nothing.
  * Bounded: bodies, stubs, journal, runs and run lifetime (a crashed worker's runs expire).
  * Re-validates every setup with the rules of `sbam_contracts.mocks` (a test keeps them in step).
"""

import hmac
import json
import os
import re
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

RUN_HEADER: Final = "X-Sbam-Run-Id"
CASE_HEADER: Final = "X-Sbam-Case"
MAX_BODY_BYTES: Final = 1024 * 1024
MAX_SEQUENCE: Final = 10
MAX_STUBS: Final = 200
MAX_RUN_STUBS: Final = 400
MAX_CONTROL_BYTES: Final = 8 * 1024 * 1024
JOURNAL_PATH_CHARS: Final = 256  # a tool controls the path it requests: the journal keeps a prefix
RESERVED_PATHS: Final = ("/health", "/_control")
METHODS: Final = ("GET", "POST", "PUT", "PATCH", "DELETE")
NO_BODY: Final = frozenset({204, 205, 304})
FORBIDDEN_MODES: Final = frozenset(
    {
        "proxy",
        "proxy_base_url",
        "proxy_url",
        "proxy_pass",
        "record",
        "recording",
        "record_mode",
        "passthrough",
        "pass_through",
        "forward",
        "forward_to",
        "upstream",
        "target_url",
        "base_url",
    }
)
CREDENTIAL_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}",
        r"\b(?:sk|pk|rk)[-_](?:live|test)?[-_]?[A-Za-z0-9]{20,}",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    )
)
STUB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/{}-]*$")
SEGMENT_PARAM = re.compile(r"^\{[^{}/]+\}$")


# --- validation (the rules of sbam_contracts.mocks) -----------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_strings(k) + _strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _keys(node: Any, allowed: set[str], where: str, errors: list[tuple[str, str]]) -> bool:
    if not isinstance(node, dict):
        errors.append((where, "must be an object"))
        return False
    banned = sorted(str(k) for k in node if str(k).lower() in FORBIDDEN_MODES)
    if banned:
        errors.append((where, f"{', '.join(banned)}: mocks never forward or record"))
        return False
    extra = sorted(str(k) for k in node if k not in allowed)
    if extra:
        errors.append((where, f"unknown key {extra[0]!r}"))
    return not extra


def _response(res: Any, where: str, errors: list[tuple[str, str]]) -> None:
    if not _keys(res, {"status", "body"}, where, errors):
        return
    status = res.get("status")
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        errors.append((f"{where}.status", "must be an integer from 100 to 599"))
    body = res.get("body")
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_BODY_BYTES:
        errors.append((f"{where}.body", f"is larger than {MAX_BODY_BYTES} bytes"))
    elif any(p.search(s) for s in _strings(body) for p in CREDENTIAL_PATTERNS):
        errors.append((f"{where}.body", "looks like it holds a real credential"))


def _stub(stub: Any, where: str, errors: list[tuple[str, str]]) -> None:
    if not _keys(stub, {"id", "method", "path", "case", "responses"}, where, errors):
        return
    if not (isinstance(stub.get("id"), str) and STUB_ID.match(stub["id"])):
        errors.append((f"{where}.id", "must be 1-64 letters, digits, . _ -"))
    if stub.get("method") not in METHODS:
        errors.append((f"{where}.method", "must be GET, POST, PUT, PATCH or DELETE"))
    path = stub.get("path")
    if not (isinstance(path, str) and len(path) <= 512 and PATH.match(path)):
        errors.append((f"{where}.path", "must be an absolute path without query or scheme"))
    elif ".." in path.split("/") or "//" in path:
        errors.append((f"{where}.path", "must not contain '..' or empty segments"))
    elif any(path == r or path.startswith(r + "/") for r in RESERVED_PATHS):
        errors.append((f"{where}.path", "/health and /_control belong to the service"))
    case = stub.get("case")
    if case is not None and not (isinstance(case, str) and 1 <= len(case.strip()) <= 120):
        errors.append((f"{where}.case", "must be 1-120 characters"))
    responses = stub.get("responses")
    if not (isinstance(responses, list) and 1 <= len(responses) <= MAX_SEQUENCE):
        errors.append((f"{where}.responses", f"must hold 1 to {MAX_SEQUENCE} responses"))
        return
    for i, res in enumerate(responses):
        _response(res, f"{where}.responses.{i}", errors)


def validate_setup(setup: Any) -> list[tuple[str, str]]:
    """(path, message) for every problem; empty when the setup may be installed."""
    errors: list[tuple[str, str]] = []
    if not _keys(setup, {"definitions"}, "", errors):
        return errors
    definitions = setup.get("definitions")
    if not (isinstance(definitions, list) and definitions):
        return [("definitions", "must hold at least one definition")]
    names: list[Any] = []
    ids: list[Any] = []
    all_routes: list[Any] = []
    for d, definition in enumerate(definitions):
        where = f"definitions.{d}"
        if not _keys(definition, {"name", "stubs"}, where, errors):
            continue
        name = definition.get("name")
        if not (isinstance(name, str) and 1 <= len(name.strip()) <= 200):
            errors.append((f"{where}.name", "must be 1-200 characters"))
        names.append(name)
        stubs = definition.get("stubs")
        if not (isinstance(stubs, list) and 1 <= len(stubs) <= MAX_STUBS):
            errors.append((f"{where}.stubs", f"must hold 1 to {MAX_STUBS} stubs"))
            continue
        routes = []
        for s, stub in enumerate(stubs):
            _stub(stub, f"{where}.stubs.{s}", errors)
            if isinstance(stub, dict):
                ids.append(stub.get("id"))
                routes.append((stub.get("case"), stub.get("method"), stub.get("path")))
                all_routes.append(routes[-1])
        if len(set(map(repr, routes))) != len(routes):
            errors.append((where, "two stubs answer the same method and path for the same case"))
    if len(set(map(repr, names))) != len(names):
        errors.append(("definitions", "definition names must be unique"))
    if len(ids) > MAX_RUN_STUBS:
        errors.append(("definitions", f"at most {MAX_RUN_STUBS} stubs per run"))
    if len(set(map(repr, ids))) != len(ids):
        errors.append(("definitions", "stub ids must be unique across the run"))
    if len(set(map(repr, all_routes))) != len(all_routes):
        errors.append(
            ("definitions", "two stubs answer the same method and path for the same case")
        )
    return errors


# --- the store -----------------------------------------------------------------------------------


@dataclass
class Stub:
    id: str
    method: str
    segments: tuple[str, ...]
    case: str | None
    responses: list[dict[str, Any]]
    templated: int  # how many `{param}` segments: fewer is more specific
    order: int


@dataclass
class Run:
    stubs: list[Stub]
    expires: float
    calls: dict[str, int] = field(default_factory=dict)
    journal: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0
    seen: int = 0


@dataclass(frozen=True)
class Answer:
    status: int
    body: Any
    stub_id: str | None
    index: int | None


class RunsFullError(Exception):
    """Too many runs: the caller should retry after one is deleted or expires."""


def _segments(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[1:])


def _compile(setup: Mapping[str, Any]) -> list[Stub]:
    stubs: list[Stub] = []
    for definition in setup["definitions"]:
        for raw in definition["stubs"]:
            segments = _segments(raw["path"])
            stubs.append(
                Stub(
                    id=raw["id"],
                    method=raw["method"],
                    segments=segments,
                    case=None if raw.get("case") is None else raw["case"].strip(),
                    responses=raw["responses"],
                    templated=sum(1 for s in segments if SEGMENT_PARAM.match(s)),
                    order=len(stubs),
                )
            )
    return stubs


def _matches(stub: Stub, method: str, segments: tuple[str, ...]) -> bool:
    return (
        stub.method == method
        and len(stub.segments) == len(segments)
        and all(
            (SEGMENT_PARAM.match(t) is not None and s != "") or t == s
            for t, s in zip(stub.segments, segments, strict=True)
        )
    )


class Mocks:
    def __init__(
        self, run_ttl: float = 3600.0, max_runs: int = 256, journal_max: int = 2000
    ) -> None:
        self.run_ttl, self.max_runs, self.journal_max = run_ttl, max_runs, journal_max
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for run_id in [r for r, run in self._runs.items() if run.expires <= now]:
            del self._runs[run_id]

    def put(self, run_id: str, setup: Mapping[str, Any], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        stubs = _compile(setup)
        with self._lock:
            self._expire(now)
            if run_id not in self._runs and len(self._runs) >= self.max_runs:
                raise RunsFullError
            self._runs[run_id] = Run(stubs=stubs, expires=now + self.run_ttl)

    def delete(self, run_id: str) -> bool:
        with self._lock:
            return self._runs.pop(run_id, None) is not None

    def journal(self, run_id: str, now: float | None = None) -> dict[str, Any] | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            run = self._runs.get(run_id)
            if run is None:
                return None
            return {"run_id": run_id, "entries": list(run.journal), "dropped": run.dropped}

    def answer(
        self,
        run_id: str,
        case: str | None,
        method: str,
        path: str,
        now: float | None = None,
    ) -> Answer | None:
        """The stub's answer (journalled), `Answer(404, ..., None)` for an unmatched request of a
        known run, or None for a run that does not exist."""
        now = time.monotonic() if now is None else now
        segments = _segments(path)
        with self._lock:
            self._expire(now)
            run = self._runs.get(run_id)
            if run is None:
                return None
            hits = [s for s in run.stubs if _matches(s, method, segments)]
            own = [s for s in hits if case is not None and s.case == case]
            pool = own or [s for s in hits if s.case is None]
            stub = min(pool, key=lambda s: (s.templated, s.order), default=None)
            run.seen += 1
            if stub is None:
                answer = Answer(404, None, None, None)
            else:
                n = run.calls.get(stub.id, 0)
                run.calls[stub.id] = n + 1
                index = min(n, len(stub.responses) - 1)  # the last response repeats
                res = stub.responses[index]
                answer = Answer(res["status"], res.get("body"), stub.id, index)
            run.journal.append(
                {
                    "n": run.seen,
                    "case": case,
                    "method": method,
                    "path": path[:JOURNAL_PATH_CHARS],
                    "stub_id": answer.stub_id or "UNMATCHED",
                    "response_index": answer.index,
                    "status": answer.status,
                }
            )
            if len(run.journal) > self.journal_max:
                del run.journal[0]
                run.dropped += 1
            return answer


# --- HTTP ----------------------------------------------------------------------------------------

JSON = "application/json"
PROBLEM = "application/problem+json"
CONTROL = re.compile(r"^/_control/runs/([^/]+)(/journal)?$")


def problem(status: int, code: str, title: str, **extra: Any) -> dict[str, Any]:
    return {"type": "about:blank", "title": title, "status": status, "code": code, **extra}


def log(level: str, msg: str, **fields: Any) -> None:
    print(json.dumps({"level": level, "logger": "sbam_mock_api", "msg": msg, **fields}), flush=True)


def make_handler(store: Mocks, token: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "sbam-mock-api"
        sys_version = ""
        timeout = 10  # a client that stalls mid-request is dropped, not waited for

        def _send(
            self,
            status: int,
            body: Any,
            content_type: str = JSON,
            headers: Mapping[str, str] | None = None,
        ) -> None:
            data = (
                b""
                if body is None or status in NO_BODY or status < 200
                else json.dumps(body).encode()
            )
            self.send_response(status)
            if data:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _body(self, limit: int) -> bytes | None:
            """The request body, or None after answering 400/413 (too large or unreadable)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(400, problem(400, "BAD_REQUEST", "Bad Content-Length"), PROBLEM)
                return None
            if length > limit:
                self._send(
                    413, problem(413, "PAYLOAD_TOO_LARGE", "Request body too large"), PROBLEM
                )
                return None
            return self.rfile.read(length)

        def _authorized(self) -> bool:
            scheme, _, sent = (self.headers.get("Authorization") or "").partition(" ")
            return scheme == "Bearer" and hmac.compare_digest(sent.encode(), token)

        def _control(self, run_id: str, journal: str | None) -> int:
            if not self._authorized():
                self._send(401, problem(401, "UNAUTHENTICATED", "Control token required"), PROBLEM)
                return 401
            if not RUN_ID.match(run_id):
                self._send(404, problem(404, "NOT_FOUND", "Unknown run id"), PROBLEM)
                return 404
            if journal and self.command == "GET":
                found = store.journal(run_id)
                if found is None:
                    self._send(404, problem(404, "NOT_FOUND", "Unknown run"), PROBLEM)
                    return 404
                self._send(200, found)
                return 200
            if not journal and self.command == "DELETE":
                if not store.delete(run_id):
                    self._send(404, problem(404, "NOT_FOUND", "Unknown run"), PROBLEM)
                    return 404
                self._send(204, None)
                return 204
            if not journal and self.command == "PUT":
                raw = self._body(MAX_CONTROL_BYTES)
                if raw is None:
                    return 400
                try:
                    setup = json.loads(raw)
                except ValueError:
                    self._send(400, problem(400, "BAD_REQUEST", "Body is not JSON"), PROBLEM)
                    return 400
                errors = validate_setup(setup)
                if errors:
                    detail = [{"path": p, "message": m} for p, m in errors[:50]]
                    self._send(
                        422,
                        problem(422, "VALIDATION_ERROR", "Invalid mock setup", errors=detail),
                        PROBLEM,
                    )
                    return 422
                try:
                    store.put(run_id, setup)
                except RunsFullError:
                    self._send(429, problem(429, "MOCK_RUNS_FULL", "Too many live runs"), PROBLEM)
                    return 429
                self._send(204, None)
                return 204
            self._send(405, problem(405, "METHOD_NOT_ALLOWED", "Method not allowed"), PROBLEM)
            return 405

        def _data(self, path: str) -> tuple[int, str | None]:
            if self._body(MAX_BODY_BYTES) is None:  # read and drop, so the connection stays sane
                return 413, None
            run_id = self.headers.get(RUN_HEADER) or ""
            if not RUN_ID.match(run_id):
                self._send(
                    400,
                    problem(400, "MOCK_RUN_REQUIRED", f"Send a valid {RUN_HEADER} header"),
                    PROBLEM,
                )
                return 400, None
            case = (self.headers.get(CASE_HEADER) or "").strip()[:120] or None
            answer = store.answer(run_id, case, self.command, path)
            if answer is None:
                self._send(
                    404,
                    problem(404, "MOCK_RUN_UNKNOWN", "No mocks are set up for this run"),
                    PROBLEM,
                )
                return 404, None
            if answer.stub_id is None:
                body = problem(404, "MOCK_UNMATCHED", "No stub matches this request")
                self._send(404, body, PROBLEM)
                return 404, None
            self._send(answer.status, answer.body, JSON, {"X-Sbam-Mock-Stub": answer.stub_id})
            return answer.status, answer.stub_id

        def _handle(self) -> None:
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path == "/health" and self.command == "GET":
                self._send(200, {"status": "ok"})
                return
            control = CONTROL.match(path)
            if path.startswith("/_control"):
                status = (
                    self._control(control[1], control[2]) if control else self._unknown_control()
                )
                log("INFO", "control", method=self.command, status=status)
                return
            if not path.startswith(
                "/"
            ):  # absolute-form (`GET http://real.host/x`): a proxy request
                self._send(
                    400,
                    problem(400, "MOCK_BAD_TARGET", "Only origin-form paths are served"),
                    PROBLEM,
                )
                log("INFO", "request", method=self.command, status=400)
                return
            status, stub = self._data(path)
            log("INFO", "request", method=self.command, path=path, status=status, stub=stub)

        def _unknown_control(self) -> int:
            if not self._authorized():
                self._send(401, problem(401, "UNAUTHENTICATED", "Control token required"), PROBLEM)
                return 401
            self._send(404, problem(404, "NOT_FOUND", "Not found"), PROBLEM)
            return 404

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815 - http.server names

        def log_message(self, fmt: str, *args: object) -> None:  # replaced by the JSON lines above
            return

    return Handler


def make_server(host: str, port: int, store: Mocks, token: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store, token.encode()))
    server.daemon_threads = True
    return server


def read_token() -> str:
    path = os.environ.get("MOCK_CONTROL_TOKEN_FILE")
    token = (
        open(path, encoding="utf-8").read().strip()
        if path
        else os.environ.get("MOCK_CONTROL_TOKEN", "")
    )
    if len(token) < 16:
        sys.exit("MOCK_CONTROL_TOKEN (or _FILE) is required and must be at least 16 characters")
    return token


HOST = "0.0.0.0"  # noqa: S104 - container port, internal networks only

if __name__ == "__main__":
    store = Mocks(
        run_ttl=float(os.environ.get("MOCK_RUN_TTL_SECONDS", "3600")),
        max_runs=int(os.environ.get("MOCK_MAX_RUNS", "256")),
        journal_max=int(os.environ.get("MOCK_JOURNAL_MAX", "2000")),
    )
    make_server(
        HOST, int(os.environ.get("MOCK_API_PORT", "8090")), store, read_token()
    ).serve_forever()
