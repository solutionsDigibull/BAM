"""The code under test's own process (#625, ADR 0025).

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
from typing import Any

MAX_REQUEST: int = 1 << 20


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
