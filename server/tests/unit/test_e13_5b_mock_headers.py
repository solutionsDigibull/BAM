"""E13.5b (#545): every HTTP call the tool makes to the mock API carries the run and the case.

The headers are added by the runner's child process (`child_server.route_mock_calls`), not by
generated code. A throwaway local HTTP server stands in for the mock API and for "any other host".
"""

import asyncio
import http.client
import importlib.util
import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
RUN, CASE = "run-545-aaaa", "retry-case"
FORGED = {"X-Sbam-Run-Id": "run-of-someone-else", "X-Sbam-Case": "someone-elses-case"}


@pytest.fixture(scope="module")
def child_server() -> ModuleType:
    path = ROOT / "compose" / "sandbox-runner" / "harness" / "child_server.py"
    spec = importlib.util.spec_from_file_location("child_server_545", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _restore_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """route_mock_calls patches classes of this process; undo it after each test."""
    for owner, name in (
        (http.client.HTTPConnection, "_send_request"),
        (httpx.Client, "_send_single_request"),
        (httpx.AsyncClient, "_send_single_request"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))


class Echo(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({k.lower(): v for k, v in self.headers.items()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return None


@pytest.fixture
def servers() -> Iterator[tuple[int, int]]:
    started = [HTTPServer(("127.0.0.1", 0), Echo) for _ in range(2)]
    for server in started:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield started[0].server_port, started[1].server_port
    for server in started:
        server.shutdown()
        server.server_close()


def environ(port: int, case: str | None = CASE) -> dict[str, str]:
    env = {"SBAM_MOCK_BASE_URL": f"http://127.0.0.1:{port}", "SBAM_RUN_ID": RUN}
    if case:
        env["SBAM_CASE"] = case
    return env


def via_urllib(port: int, headers: dict[str, str] | None = None) -> dict[str, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/crm", headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310
        seen: dict[str, str] = json.loads(res.read())
        return seen


def via_httpx(port: int, headers: dict[str, str] | None = None) -> dict[str, str]:
    seen: dict[str, str] = httpx.get(f"http://127.0.0.1:{port}/crm", headers=headers).json()
    return seen


def via_async_httpx(port: int, headers: dict[str, str] | None = None) -> dict[str, str]:
    async def call() -> dict[str, str]:
        async with httpx.AsyncClient() as client:
            seen: dict[str, str] = (
                await client.get(f"http://127.0.0.1:{port}/crm", headers=headers)
            ).json()
            return seen

    return asyncio.run(call())


CLIENTS = [via_urllib, via_httpx, via_async_httpx]


@pytest.mark.parametrize("call", CLIENTS)
def test_e13_5b_a_call_to_the_mock_carries_the_run_and_the_case(
    child_server: ModuleType, servers: tuple[int, int], call: Any
) -> None:
    assert child_server.route_mock_calls(environ(servers[0])) is True
    seen = call(servers[0])
    assert seen["x-sbam-run-id"] == RUN and seen["x-sbam-case"] == CASE


@pytest.mark.parametrize("call", CLIENTS)
def test_e13_5b_a_tool_cannot_choose_another_runs_namespace(
    child_server: ModuleType, servers: tuple[int, int], call: Any
) -> None:
    child_server.route_mock_calls(environ(servers[0]))
    seen = call(servers[0], FORGED)
    assert seen["x-sbam-run-id"] == RUN and seen["x-sbam-case"] == CASE


@pytest.mark.parametrize("call", CLIENTS)
def test_e13_5b_another_host_never_learns_the_run(
    child_server: ModuleType, servers: tuple[int, int], call: Any
) -> None:
    child_server.route_mock_calls(environ(servers[0]))
    seen = call(servers[1], FORGED)  # same address, another port: not the mock
    assert "x-sbam-run-id" not in seen and "x-sbam-case" not in seen


@pytest.mark.parametrize("call", CLIENTS)
def test_e13_5b_without_a_case_only_the_run_header_is_sent(
    child_server: ModuleType, servers: tuple[int, int], call: Any
) -> None:
    child_server.route_mock_calls(environ(servers[0], case=None))
    seen = call(servers[0], {"X-Sbam-Case": "forged"})
    assert seen["x-sbam-run-id"] == RUN and "x-sbam-case" not in seen


def test_e13_5b_a_run_without_a_mock_api_changes_nothing(
    child_server: ModuleType, servers: tuple[int, int]
) -> None:
    before = http.client.HTTPConnection._send_request  # type: ignore[attr-defined]
    assert child_server.route_mock_calls({"SBAM_RUN_ID": RUN}) is False
    assert child_server.route_mock_calls({"SBAM_MOCK_BASE_URL": "http://x:1"}) is False
    assert http.client.HTTPConnection._send_request is before  # type: ignore[attr-defined]
    assert "x-sbam-run-id" not in via_urllib(servers[0])
