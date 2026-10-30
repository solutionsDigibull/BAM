"""The in-VM runner: one code attempt, every case of one dataset, one `outputs.json` (E13.2, ADR 0023 d.7).

    python -I /opt/sbam/harness/run_cases.py /work/request.json

It runs inside the sandbox as the unprivileged user, next to the E13.3 harness and what the harness
imports (vendored under /opt/sbam/harness, see `vendor_files.py` in the tests). It reads the request,
starts an ephemeral PostgreSQL, applies `schema.sql`, and gives every case its own database made from
that schema. For each case it seeds the fresh state, imports the bundle's FastMCP server, runs the
harness against it and records the trace plus the handoff rows the database really holds.

Rules worth knowing
  * Exit codes are a protocol (`classify.py`): 0 outputs written (cases may still have failed), 71 the
    ephemeral database could not be set up, 72 the dataset cannot be run (a seed statement failed, or a
    case the harness refuses). Anything else is the runner itself failing; `outputs.json` is then absent.
  * Test failures are never exceptions of the runner: a bundle that does not import, a tool that raises,
    overruns or prints instead of returning, each ends as a case with an error category and exit 0.
  * The bundle appends its own handoff rows (the generated action tool calls `state_handoff.append`).
    The runner therefore does not insert: it checks, after every pass, that exactly one new row exists and
    that it carries every required field. A bundle that skips or doubles the append fails its case.
  * A hung tool's thread cannot be killed, and FastMCP will not cancel a synchronous tool until it
    returns. So a call that overruns is *abandoned*: the harness gets its timeout at once, the thread is
    left behind, and the loop that runs it is never closed (closing would wait for it). Because a left-behind
    thread may wake later and touch module state, every case imports its own copy of the bundle, bound to
    its own database connection. The runner ends with `os._exit` once the outputs file is on disk.
"""

import asyncio
import importlib
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Final

# `python -I` leaves the script's directory off sys.path; the vendored packages live next to this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pydantic import JsonValue  # noqa: E402
from sbam_contracts.harness import HarnessError, HarnessTrace  # noqa: E402
from sbam_contracts.isatvon14 import NNotification  # noqa: E402
from sbam_contracts.sandbox import (  # noqa: E402
    RunnerCaseResult,
    RunnerOutputs,
    RunnerRequest,
    RunnerResources,
    normalize_digest,
)
from sbam_workers.isatvon.loop_spec import load_loop_spec  # noqa: E402
from sbam_workers.test.harness import (  # noqa: E402
    ITERATION_FIELD,
    HandoffSpec,
    LoopHarnessError,
    LoopTestCase,
    run_case,
)

EXIT_OK: Final = 0
EXIT_REQUEST: Final = 2
EXIT_DATABASE: Final = 71
EXIT_SEED: Final = 72
RUNTIME_FILE: Final = "/etc/sbam-runtime"
TEMPLATE_DB: Final = "sbam_tpl"
MAX_DUMPED_ROWS: Final = 1000
SQL_STEP_KEY: Final = "sql"  # a state_script change: {"sql": [statements]}


class SeedError(Exception):
    """A statement of a case's seed SQL failed: the dataset, not the code, is wrong."""


class HandoffError(Exception):
    """The bundle did not append exactly one well-formed handoff row in a pass."""


def runtime_digest() -> str:
    for source in (
        Path(RUNTIME_FILE).read_text() if Path(RUNTIME_FILE).exists() else "",
        os.environ.get("SANDBOX_RUNTIME_DIGEST", ""),
    ):
        try:
            return normalize_digest(source.strip())
        except ValueError:
            continue
    raise RuntimeError("the runtime reports no digest")


class PgStore:
    """The case's database as the harness's `StateStore`. Fresh state comes through the bundle's own
    `state.read`; handoff rows are written by the bundle and only checked here."""

    def __init__(
        self,
        conn: Any,
        table: str,
        key_field: str,
        inputs: Mapping[str, Any],
        state_module: Any,
        required: list[str],
    ) -> None:
        self.conn, self.table, self.required = conn, table, required
        self.key, self.state, self.seen = inputs.get(key_field), state_module, 0

    def read_state(self, names: list[str]) -> dict[str, Any]:
        fresh = self.state.read(self.key)
        return {name: getattr(fresh, name) for name in names}

    def apply_script(self, changes: Mapping[str, JsonValue]) -> None:
        for statement in changes.get(SQL_STEP_KEY) or []:  # type: ignore[union-attr]
            self.conn.execute(str(statement))

    def rows(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(f'SELECT payload FROM "{self.table}" ORDER BY id')  # noqa: S608
        return [dict(row[0]) for row in cursor.fetchall()]

    def append_handoff(self, table: str, row: Mapping[str, Any]) -> None:
        rows = self.rows()
        self.seen += 1
        if len(rows) != self.seen:
            raise HandoffError(
                f"pass {self.seen} left {len(rows)} handoff rows; exactly {self.seen} were expected"
            )
        missing = [f for f in self.required if f not in rows[-1]]
        if missing:
            raise HandoffError(f"the handoff row misses required fields {missing}")


def memory_error_in(exc: BaseException | None) -> bool:
    """FastMCP wraps what a tool raised in a ToolError whose message is `str(original)`: empty for a
    MemoryError. The original is only on the exception chain, so this is read on the server side."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MemoryError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def add_memory_marker(mcp: Any) -> None:
    """Make a tool that ran out of memory say so in the error the harness will read."""
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware

    class MemoryMarker(Middleware):  # type: ignore[misc]
        async def on_call_tool(self, context: Any, call_next: Any) -> Any:
            try:
                return await call_next(context)
            except ToolError as exc:
                if memory_error_in(exc):
                    raise ToolError("MemoryError: the tool exhausted its memory") from exc
                raise

    mcp.add_middleware(MemoryMarker())


class McpTools:
    """The bundle's FastMCP server called in memory, the way a client would call it.

    The harness hands every tool the case inputs, the pass number and (for the check tool) the action's
    outputs. A generated tool declares only its own contract inputs and FastMCP rejects anything else, so
    each call is cut down to the parameters the tool declares. A call the harness cancels is abandoned,
    not awaited (see the module docstring); `abandoned` tells the caller not to close the client."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.declared: dict[str, set[str]] | None = None
        self.abandoned = False

    async def parameters(self, tool: str) -> set[str] | None:
        if self.declared is None:
            self.declared = {
                t.name: set((t.input_schema or {}).get("properties", {}))
                for t in await self.client.list_tools()
            }
        return self.declared.get(tool)

    async def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
        allowed = await self.parameters(tool)
        if allowed is not None:
            arguments = {k: v for k, v in arguments.items() if k in allowed}
        pending = asyncio.ensure_future(self.client.call_tool(tool, dict(arguments)))
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError:
            self.abandoned = True  # leave `pending` running; its thread may still be asleep
            raise
        return result.data  # type: ignore[no-any-return]  # a tool that prints returns None: the harness refuses it


def error_trace(case_id: str, category: str, message: str, *, budget: bool = False) -> HarnessTrace:
    return HarnessTrace(
        case_id=case_id,
        terminal=None,
        iterations=0,
        passes=[],
        ui_updates=[],
        handoff_rows=0,
        error=HarnessError.model_validate(
            {
                "category": category,
                "pass": 1,
                "step": "action",
                "message": message[:300],
                "budget_exceeded": budget,
                "elapsed_ms": 0,
            }
        ),
    )


def as_oom(trace: HarnessTrace) -> HarnessTrace:
    """A tool that died of MemoryError is OOM, not a generic runtime error."""
    if trace.error is not None and "MemoryError: the tool exhausted" in trace.error.message:
        error = trace.error.model_copy(update={"category": "OOM"})
        return trace.model_copy(update={"error": error})
    return trace


def loop_case(raw: Mapping[str, Any], request: RunnerRequest) -> LoopTestCase:
    steps = {
        index + 2: {SQL_STEP_KEY: list(statements)}  # step i runs before pass i + 2
        for index, statements in enumerate(raw.get("state_script") or [])
    }
    return LoopTestCase(
        case_id=str(raw["case_id"]),
        kind=raw["kind"],
        expected_terminal=raw["expected_terminal"],
        inputs=dict(raw["input"]),
        state_script=steps,  # type: ignore[arg-type]
        action_tool=request.harness.action_tool,
        fixed_check_tool=request.harness.fixed_check_tool,
    )


def notification(request: RunnerRequest) -> NNotification:
    return NNotification(
        ui_updates=list(request.harness.ui_updates), database_updates=[], alerts=[], next=None
    )


class Database:
    """The ephemeral PostgreSQL: started once, one database per case made from the schema."""

    def __init__(self, schema_sql: str) -> None:
        self.schema_sql, self.datadir, self.admin = schema_sql, "", None

    def start(self) -> None:
        import psycopg

        self.datadir = tempfile.mkdtemp(prefix="sbam-pg-")
        started = subprocess.run(  # noqa: S603 - fixed command, no input
            ["sbam-pg-start"],  # noqa: S607
            env={**os.environ, "PGDATA": self.datadir},
            capture_output=True,
            timeout=60,
            check=False,
        )
        if started.returncode != 0:
            raise RuntimeError("the ephemeral database did not start")
        self.admin = psycopg.connect(
            host="127.0.0.1", user="postgres", dbname="postgres", autocommit=True
        )
        self.admin.execute(f"CREATE DATABASE {TEMPLATE_DB}")
        with psycopg.connect(
            host="127.0.0.1", user="postgres", dbname=TEMPLATE_DB, autocommit=True
        ) as conn:
            conn.execute(self.schema_sql)

    def case(self, number: int) -> Any:
        import psycopg

        name = f"sbam_case_{number}"
        self.admin.execute(f'CREATE DATABASE "{name}" TEMPLATE {TEMPLATE_DB}')
        return psycopg.connect(host="127.0.0.1", user="postgres", dbname=name, autocommit=True)

    def stop(self) -> None:
        try:
            if self.admin is not None:
                self.admin.close()
            if self.datadir:
                subprocess.run(  # noqa: S603
                    ["pg_ctl", "-D", self.datadir, "stop", "-m", "immediate"],  # noqa: S607
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
        finally:
            shutil.rmtree(self.datadir, ignore_errors=True)


def import_bundle(bundle_dir: Path, tool_module: str) -> tuple[Any, Any, Any]:
    """A fresh copy of the bundle's `mcp`, `state` and `state_handoff`, or what a failed import raised."""
    if str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
    package = tool_module.rpartition(".")[0]
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        del sys.modules[
            name
        ]  # a left-behind thread keeps the old copy, bound to the old connection
    server = importlib.import_module(tool_module)
    prefix = f"{package}." if package else ""
    return (
        server.mcp,
        importlib.import_module(f"{prefix}state"),
        importlib.import_module(f"{prefix}state_handoff"),
    )


async def run_case_in_db(
    request: RunnerRequest, raw: Mapping[str, Any], db: Database, number: int, bundle_dir: Path
) -> tuple[HarnessTrace, list[dict[str, Any]]]:
    from fastmcp import Client

    conn = db.case(number)
    try:
        for statement in raw.get("initial_state") or []:
            try:
                conn.execute(str(statement))
            except Exception as exc:
                raise SeedError(f"{type(exc).__name__}") from None
        try:
            mcp, state, handoff = import_bundle(bundle_dir, request.tool_module)
        except BaseException as exc:  # a bundle may raise anything, SystemExit included
            return error_trace(
                str(raw["case_id"]), "IMPORT_ERROR", f"{type(exc).__name__}: {exc}"
            ), []
        add_memory_marker(mcp)
        state.configure(conn)
        handoff.configure(conn)
        store = PgStore(
            conn,
            request.handoff_table,
            request.harness.key_field,
            raw["input"],
            state,
            request.harness.handoff_fields,
        )
        spec = load_loop_spec(request.loop_spec)
        config = request.harness
        client = Client(mcp)
        await client.__aenter__()
        tools = McpTools(client)
        try:
            trace = await asyncio.wait_for(
                run_case(
                    spec,
                    loop_case(raw, request),
                    fresh_state_parameters=config.fresh_state_parameters,
                    # the harness only needs a row to hand over; what the table must hold is checked on the table
                    handoff=HandoffSpec(
                        table=request.handoff_table, required_fields=[ITERATION_FIELD]
                    ),
                    notification=notification(request),
                    tools=tools,
                    store=store,
                    required_inputs=config.required_inputs,
                ),
                request.limits.case_timeout_s,
            )
        finally:
            if not tools.abandoned:  # closing would wait for the tool that is still running
                await client.__aexit__(None, None, None)
        try:
            rows = store.rows()[:MAX_DUMPED_ROWS]
        except Exception:
            rows = []
        return as_oom(trace), rows
    finally:
        conn.close()


def result_of(
    raw: Mapping[str, Any], trace: HarnessTrace, rows: list[dict[str, Any]]
) -> RunnerCaseResult:
    expected = raw["expected_terminal"]
    passed = trace.passed(expected)
    category = None
    if trace.error is not None:
        category = trace.error.category
    elif not passed:
        category = "ASSERTION_FAILED"
    return RunnerCaseResult.model_validate(
        {
            "case_id": str(raw["case_id"]),
            "expected_terminal": expected,
            "terminal": trace.terminal,
            "passed": passed,
            "failure_category": category,
            "trace": json.loads(
                trace.model_dump_json(by_alias=True)
            ),  # `terminal: null` stays: it is required
            "handoff_rows": rows,
        }
    )


def fit(outputs: RunnerOutputs, limit: int) -> RunnerOutputs:
    """Never write a file the worker will refuse to read: drop the dumps, then the pass detail."""
    if len(outputs.model_dump_json()) <= limit:
        return outputs
    slim = [c.model_copy(update={"handoff_rows": [], "truncated": True}) for c in outputs.cases]
    outputs = outputs.model_copy(update={"cases": slim})
    if len(outputs.model_dump_json()) <= limit:
        return outputs
    bare = [
        c.model_copy(update={"trace": {**c.trace, "passes": [], "ui_updates": []}}) for c in slim
    ]
    return outputs.model_copy(update={"cases": bare})


def write_outputs(path: Path, outputs: RunnerOutputs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(outputs.model_dump_json())
    temp.replace(path)  # the file either exists complete or not at all


def limit_memory(memory_mb: int) -> None:
    """Make an allocation bomb a MemoryError the harness reports as OOM, not a killed runner."""
    try:
        cap = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ValueError, OSError):
        pass  # the VM's own limit still applies


def run_sync(coro: Awaitable[Any]) -> Any:
    """Run to completion on a loop that is never closed: `asyncio.run` would cancel and then wait for an
    abandoned tool's task, which is exactly the wait that must not happen."""
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


def run_all(
    request: RunnerRequest, db: Database, started: float, bundle_dir: Path
) -> RunnerOutputs:
    """Every case in name order. Raises `SeedError` / `LoopHarnessError` for a dataset that cannot run."""
    results: list[RunnerCaseResult] = []
    import fastmcp  # noqa: F401 - loaded before the memory cap: the cap is for the bundle, not for us

    limit_memory(request.limits.memory_mb)
    for number, raw in enumerate(request.cases):
        try:
            trace, rows = run_sync(run_case_in_db(request, raw, db, number, bundle_dir))
        except TimeoutError:
            trace = error_trace(
                str(raw["case_id"]),
                "TIMEOUT",
                f"the case overran its {request.limits.case_timeout_s} s limit",
                budget=True,
            )
            rows = []
        results.append(result_of(raw, trace, rows))
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return RunnerOutputs(
        runtime_digest=runtime_digest(),
        cases=results,
        resources=RunnerResources(
            wall_ms=round((time.monotonic() - started) * 1000),
            cpu_ms=round((usage.ru_utime + usage.ru_stime) * 1000),
            peak_rss_kb=int(usage.ru_maxrss),
        ),
        mode=request.mode,
    )


def main(argv: list[str]) -> int:
    started = time.monotonic()
    try:
        request = RunnerRequest.model_validate_json(Path(argv[1]).read_text())
        schema_sql = (Path(argv[1]).parent / "schema.sql").read_text()
    except (IndexError, OSError, ValueError) as exc:
        print(f"bad request: {type(exc).__name__}", file=sys.stderr)
        return EXIT_REQUEST
    db = Database(schema_sql)
    try:
        try:
            db.start()
        except Exception as exc:
            print(f"database setup failed: {type(exc).__name__}", file=sys.stderr)
            return EXIT_DATABASE
        try:
            outputs = run_all(request, db, started, Path(argv[1]).parent / "bundle")
        except (SeedError, LoopHarnessError) as exc:
            print(f"dataset cannot run: {exc}", file=sys.stderr)
            return EXIT_SEED
        out = Path(argv[1]).parent / "out" / "outputs.json"
        write_outputs(out, fit(outputs, request.limits.max_output_bytes))
        return EXIT_OK
    finally:
        db.stop()


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # a hung tool's thread must not keep the VM's process alive
