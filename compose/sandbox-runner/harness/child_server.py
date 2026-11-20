"""The code under test's own process (#625, ADR 0027).

    python -I child_server.py <bundle_dir> <tool_module> <database> <request_fd> <reply_fd> <memory_mb>

The runner (`run_cases.py`) is the trusted side: it holds the harness, reads the fresh state, judges
every pass and writes `outputs.json`. Everything the LLM wrote runs here instead, in a process of its
own, so it cannot patch the harness, replace `write_outputs` or reach the trace: it only ever sees tool
calls and answers them.

The channel is two inherited pipes (`request_fd`, `reply_fd`), one JSON object per line. The child's
stdin, stdout and stderr are /dev/null, so a tool that prints cannot corrupt it.

    {"op": "parameters"}                          -> {"ok": true, "value": {tool: [parameter, ...]}}
    {"op": "call", "tool": t, "arguments": {...}} -> {"ok": true, "value": <the tool's JSON object>}
                                                      {"ok": false, "message": "..."}  (the tool failed)

The first line the child writes is the handshake: `{"ok": true}` when the bundle imported, or
`{"ok": false, "message": "<ExceptionType>: <text>"}` when it did not (an import error is a failed
case, never a failed runner). Anything the child does to this channel, the runner treats as the code
under test failing: it never trusts a reply for more than "this is what the tool answered".

Only the standard library is imported at module level: the runner imports `memory_error_in` from here.
"""

import asyncio
import importlib
import json
import os
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

MAX_REQUEST: int = 1 << 20
RUN_HEADER: str = (
    "X-Sbam-Run-Id"  # sbam_contracts.mocks.RUN_HEADER (not importable here)
)
CASE_HEADER: str = "X-Sbam-Case"  # sbam_contracts.mocks.CASE_HEADER


def memory_error_in(exc: BaseException | None) -> bool:
    """FastMCP wraps what a tool raised in a ToolError whose message is `str(original)`: empty for a
    MemoryError. The original is only on the exception chain, so this is read on the server side.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MemoryError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def route_mock_calls(environ: Mapping[str, str] = os.environ) -> bool:
    """Every HTTP call this process makes to the mock API carries the run and the case (E13.5b, #545).

    The tool calls the API itself, so the headers are added here, in the trusted harness that wraps
    the tool, and not in generated code: nothing a model wrote has to remember them, and nothing it
    wrote can choose another run's namespace, because the values are *set* over whatever the tool
    sent. Only calls to `SBAM_MOCK_BASE_URL`'s host and port get them; a call to any other host loses
    these two headers if the tool set them (the egress policy decides what it may reach). Covers `urllib` and
    `http.client`, and `httpx` (sync and async), which FastMCP brings; a tool that opens a raw
    socket sends no header and the mock answers MOCK_UNMATCHED, a visible finding.
    Returns False when the run has no mock API."""
    base, run = environ.get("SBAM_MOCK_BASE_URL"), environ.get("SBAM_RUN_ID")
    if not base or not run:
        return False
    parts = urlsplit(base)
    mock_host, mock_port = (parts.hostname or "").lower(), parts.port or 80
    stamp = {RUN_HEADER: run}
    if environ.get("SBAM_CASE"):
        stamp[CASE_HEADER] = environ["SBAM_CASE"]
    ours = {name.lower() for name in stamp} | {RUN_HEADER.lower(), CASE_HEADER.lower()}

    def is_mock(host: Any, port: Any) -> bool:
        return str(host or "").lower() == mock_host and int(port or 80) == mock_port

    import http.client

    original_send = http.client.HTTPConnection._send_request  # type: ignore[attr-defined]

    def send_request(
        self: Any, method: Any, url: Any, body: Any, headers: Any, *args: Any
    ) -> Any:
        kept = {k: v for k, v in dict(headers).items() if k.lower() not in ours}
        headers = {**kept, **stamp} if is_mock(self.host, self.port) else kept
        return original_send(self, method, url, body, headers, *args)

    http.client.HTTPConnection._send_request = send_request  # type: ignore[attr-defined]

    import httpx

    def stamped(original: Any) -> Any:
        def single(
            self: Any, request: Any
        ) -> Any:  # once per hop, so a redirect is checked too
            for name in ours:
                request.headers.pop(
                    name, None
                )  # never the tool's own, never for another host
            if is_mock(request.url.host, request.url.port or 80):
                for name, value in stamp.items():
                    request.headers[name] = value
            return original(self, request)

        return single

    httpx.Client._send_single_request = stamped(httpx.Client._send_single_request)  # type: ignore[method-assign]
    httpx.AsyncClient._send_single_request = stamped(httpx.AsyncClient._send_single_request)  # type: ignore[method-assign]
    return True


def add_memory_marker(mcp: Any) -> None:
    """Make a tool that ran out of memory say so in the error the runner will read."""
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware

    class MemoryMarker(Middleware):  # type: ignore[misc]
        async def on_call_tool(self, context: Any, call_next: Any) -> Any:
            try:
                return await call_next(context)
            except ToolError as exc:
                if memory_error_in(exc):
                    raise ToolError(
                        "MemoryError: the tool exhausted its memory"
                    ) from exc
                raise

    mcp.add_middleware(MemoryMarker())


def limit_memory(memory_mb: int) -> None:
    """Make an allocation bomb a MemoryError the runner reports as OOM, not a killed process."""
    try:
        import resource

        cap = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ValueError, OSError):
        pass  # the VM's own limit still applies


def import_bundle(bundle_dir: str, tool_module: str) -> tuple[Any, Any, Any]:
    """The bundle's `mcp`, `state` and `state_handoff`, or what a failed import raised."""
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)
    server = importlib.import_module(tool_module)
    package = tool_module.rpartition(".")[0]
    prefix = f"{package}." if package else ""
    return (
        server.mcp,
        importlib.import_module(f"{prefix}state"),
        importlib.import_module(f"{prefix}state_handoff"),
    )


class Channel:
    def __init__(self, request_fd: int, reply_fd: int) -> None:
        self.requests = os.fdopen(request_fd, "rb")
        self.replies = os.fdopen(reply_fd, "wb")

    def read(self) -> dict[str, Any] | None:
        line = self.requests.readline(MAX_REQUEST)
        if not line or not line.endswith(b"\n"):
            return None  # the runner closed the pipe, or sent more than it ever should
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else None

    def send(self, message: dict[str, Any]) -> None:
        self.replies.write(json.dumps(message, default=str).encode() + b"\n")
        self.replies.flush()


async def serve(channel: Channel, client: Any) -> None:
    from pydantic_core import to_jsonable_python

    while (request := channel.read()) is not None:
        op = request.get("op")
        try:
            if op == "parameters":
                tools = await client.list_tools()
                value: Any = {
                    t.name: sorted((t.input_schema or {}).get("properties", {}))
                    for t in tools
                }
            elif op == "call":
                result = await client.call_tool(
                    str(request["tool"]), dict(request["arguments"])
                )
                value = to_jsonable_python(result.data, fallback=str)
            else:
                return
            channel.send({"ok": True, "value": value})
        except Exception as exc:  # a tool failure is the case's, reported by name
            channel.send(
                {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:2000]}
            )


def main(argv: list[str]) -> int:
    bundle_dir, tool_module, database = argv[1], argv[2], argv[3]
    channel = Channel(int(argv[4]), int(argv[5]))
    try:
        import psycopg
        from fastmcp import Client
        from pydantic_core import (
            to_jsonable_python,
        )  # noqa: F401 - loaded before the memory cap

        limit_memory(
            int(argv[6])
        )  # the cap is for the bundle, not for the libraries above
        conn = psycopg.connect(
            host="127.0.0.1", user="postgres", dbname=database, autocommit=True
        )
        route_mock_calls()
        mcp, state, handoff = import_bundle(bundle_dir, tool_module)
        add_memory_marker(mcp)
        state.configure(conn)
        handoff.configure(conn)
    except BaseException as exc:  # a bundle may raise anything, SystemExit included
        channel.send({"ok": False, "message": f"{type(exc).__name__}: {exc}"[:2000]})
        return 0
    channel.send({"ok": True})
    loop = asyncio.new_event_loop()

    async def run() -> None:
        async with Client(mcp) as client:
            await serve(channel, client)

    loop.run_until_complete(run())
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main(sys.argv)
    finally:
        os._exit(code)  # a hung tool's thread must not keep this process alive
