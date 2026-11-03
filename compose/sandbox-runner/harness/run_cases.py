"""The in-VM runner: one code attempt, every case of one dataset, one `outputs.json` (E13.2, ADR 0023 d.7).

    python -I /opt/sbam/harness/run_cases.py /work/request.json

It runs inside the sandbox as the unprivileged user, next to the E13.3 harness and what the harness
imports (vendored under /opt/sbam/harness, see `vendor_files.py` in the tests). It reads the request,
starts an ephemeral PostgreSQL, applies `schema.sql`, and gives every case its own database made from
that schema. For each case it seeds the fresh state, starts the bundle's FastMCP server in a child
process of its own (`child_server.py`), runs the harness against it over a pipe and records the trace
plus the handoff rows the database really holds.

The runner is the trusted side and never imports the bundle (#625, ADR 0027). What the LLM wrote runs in
the child, so it cannot patch the harness, replace `write_outputs` or reach the trace; the runner reads
the fresh state itself, from the literal `QUERIES` the assembler wrote. The runner marks itself
non-dumpable (no ptrace, no /proc/<pid>/mem for a same-uid child) and is a child subreaper, so a
process the code under test starts and orphans comes back to it. Before `outputs.json` is written every
process that is not the database is stopped and reaped, and the `out/` directory is made again, so
nothing the code under test left there or is still running can touch the file.

Rules worth knowing
  * Exit codes are a protocol (`classify.py`): 0 outputs written (cases may still have failed), 71 the
    ephemeral database could not be set up, 72 the dataset cannot be run (a seed statement failed, or a
    case the harness refuses). Anything else is the runner itself failing; `outputs.json` is then absent.
  * Test failures are never exceptions of the runner: a bundle that does not import, a tool that raises,
    overruns or prints instead of returning, each ends as a case with an error category and exit 0.
  * The bundle appends its own handoff rows (the generated action tool calls `state_handoff.append`).
    The runner therefore does not insert: it checks, after every pass, that exactly one new row exists and
    that it carries every required field. A bundle that skips or doubles the append fails its case.
  * A tool that hangs or overruns is a case that ends: the harness gets its timeout, and the child's
    process group is killed, so there is nothing left to wake up later and touch module state. Every
    case starts its own child, bound to its own database connection. The runner ends with `os._exit`
    once the outputs file is on disk.
"""

import asyncio
import ast
import ctypes
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

# `python -I` leaves the script's directory off sys.path; the vendored packages live next to this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from child_server import (
    memory_error_in,
)  # noqa: E402,F401 - the tests read it from here
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
EXIT_CRASH: Final = 70
EXIT_DATABASE: Final = 71
EXIT_SEED: Final = 72
RUNTIME_FILE: Final = "/etc/sbam-runtime"
TEMPLATE_DB: Final = "sbam_tpl"
MAX_DUMPED_ROWS: Final = 1000
STATEMENT_TIMEOUT_MS: Final = 10_000
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


class BundleError(Exception):
    """The bundle's `state.py` does not hold the literal `QUERIES` the runner reads state from."""


def _is_queries(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "QUERIES" for t in node.targets
    ):
        return node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "QUERIES"
    ):
        return node.value
    return None


def state_queries(bundle_dir: Path, tool_module: str) -> dict[str, str]:
    """The bundle's fresh-state queries, read as data. The assembler writes `QUERIES = {name: "SELECT
    ... %s"}` itself (the LLM only supplies the SQL strings), so the runner never needs to run the
    bundle's code to know them, and the code under test cannot make the runner see other state.
    """
    package = tool_module.rpartition(".")[0]
    path = bundle_dir / package.replace(".", "/") / "state.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise BundleError(f"state.py cannot be read: {type(exc).__name__}") from None
    for node in tree.body:
        value = _is_queries(node)
        if value is None:
            continue
        try:
            queries = ast.literal_eval(value)
        except ValueError:
            raise BundleError("QUERIES is not a literal") from None
        if not isinstance(queries, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in queries.items()
        ):
            raise BundleError("QUERIES is not a dict of SQL strings")
        return queries
    raise BundleError("state.py has no QUERIES")


class PgStore:
    """The case's database as the harness's `StateStore`. Fresh state is read here, by the runner, with
    the bundle's own queries (data, see `state_queries`); handoff rows are written by the bundle and
    only checked here."""

    def __init__(
        self,
        conn: Any,
        table: str,
        key_field: str,
        inputs: Mapping[str, Any],
        queries: Mapping[str, str],
        required: list[str],
    ) -> None:
        self.conn, self.table, self.required = conn, table, required
        self.key, self.queries, self.seen = inputs.get(key_field), queries, 0

    def read_state(self, names: list[str]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, sql in self.queries.items():
            if not sql.lstrip().upper().startswith("SELECT") or sql.count("%s") != 1:
                raise ValueError(
                    f"state query {name!r} must be a SELECT with exactly one %s"
                )
            row = self.conn.execute(sql, (self.key,)).fetchone()
            values[name] = row[0] if row else None
        return {name: values.get(name) for name in names}

    def apply_script(self, changes: Mapping[str, JsonValue]) -> None:
        for statement in changes.get(SQL_STEP_KEY) or []:  # type: ignore[union-attr]
            self.conn.execute(str(statement))

    def rows(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            f'SELECT payload FROM "{self.table}" ORDER BY id'
        )  # noqa: S608
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


# --- the code under test's process ------------------------------------------------------------------

CHILD_SCRIPT: Final = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "child_server.py"
)
CHILD_ENV_KEYS: Final = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SBAM_MODE",
    "SBAM_RUN_ID",
    "SBAM_DATA_DIR",
    "SBAM_MOCK_BASE_URL",
)
PR_SET_DUMPABLE: Final = 4
PR_SET_CHILD_SUBREAPER: Final = 36
STOP_ROUNDS: Final = 50


class ToolError(Exception):
    """What a tool (or the process it runs in) answered instead of a result; the harness names this
    class and message in the case's error, so the name stays what FastMCP's own error was called.
    """


def harden_runner() -> None:
    """The runner cannot be read or written by the code under test, and orphans come to it."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise RuntimeError("cannot mark the runner non-dumpable")
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise RuntimeError("cannot become a child subreaper")


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, state) of every process we can see."""
    table: dict[int, tuple[int, str]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read().decode("utf-8", "replace")
            fields = stat.rsplit(")", 1)[1].split()
            table[int(entry)] = (int(fields[1]), fields[0])
        except (OSError, IndexError, ValueError):
            continue  # the process ended while we looked
    return table


def below(table: Mapping[int, tuple[int, str]], root: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, (parent, _) in table.items():
        children.setdefault(parent, []).append(pid)
    found: set[int] = set()
    queue = [root]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def bundle_processes(keep_root: int | None) -> list[int]:
    """Every live process below the runner except the database's (the postmaster and its tree)."""
    table = process_table()
    found = below(table, os.getpid())
    if keep_root is not None:
        found -= {keep_root} | below(table, keep_root)
    return sorted(p for p in found if table[p][1] != "Z")


def stop_bundle_processes(keep_root: int | None) -> None:
    """Freeze, kill and reap everything the code under test started, however it hid, until none is
    left. Raises when something survives: `outputs.json` must never be written next to a live one.
    """
    for _ in range(STOP_ROUNDS):
        pids = bundle_processes(keep_root)
        if not pids:
            return
        for sig in (
            signal.SIGSTOP,
            signal.SIGKILL,
        ):  # frozen first: it cannot fork while we count
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    continue
        for pid in pids:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass  # not ours to reap
        time.sleep(0.01)
    if bundle_processes(keep_root):
        raise RuntimeError("processes of the code under test survive a kill")


class ChildGone(Exception):
    """The code under test's process ended, or broke the channel, instead of answering."""


class ChildProcess:
    """The bundle's FastMCP server in a process of its own, spoken to over two pipes (child_server.py).

    A pipe call blocks a thread, never the loop; a call the harness cancels is simply followed by
    `stop()`, which ends the process group and with it the blocked read."""

    def __init__(
        self, bundle_dir: Path, tool_module: str, database: str, limits: Any
    ) -> None:
        request_read, request_write = os.pipe()
        reply_read, reply_write = os.pipe()
        self.max_reply = limits.max_output_bytes
        env = {k: os.environ[k] for k in CHILD_ENV_KEYS if k in os.environ}
        argv = [
            sys.executable, "-I", CHILD_SCRIPT, str(bundle_dir), tool_module, database,
            str(request_read), str(reply_write), str(limits.memory_mb),
        ]  # fmt: skip
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=bundle_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(request_read, reply_write),
                start_new_session=True,
            )
        except BaseException:
            for fd in (request_read, request_write, reply_read, reply_write):
                os.close(fd)
            raise
        os.close(request_read)
        os.close(reply_write)
        self.requests = os.fdopen(request_write, "wb")
        self.replies = os.fdopen(reply_read, "rb")
        self.pool = ThreadPoolExecutor(max_workers=1)

    def _read(self) -> dict[str, Any]:
        line = self.replies.readline(self.max_reply + 1)
        if not line.endswith(b"\n"):
            raise ChildGone(
                "the code under test's process ended or sent an oversized reply"
            )
        try:
            reply = json.loads(line)
        except ValueError:
            raise ChildGone("the code under test broke the channel") from None
        if not isinstance(reply, dict):
            raise ChildGone("the code under test broke the channel")
        return reply

    def _exchange(self, message: Mapping[str, Any] | None) -> dict[str, Any]:
        try:
            if message is not None:
                self.requests.write(json.dumps(message).encode() + b"\n")
                self.requests.flush()
            return self._read()
        except (OSError, ValueError):
            raise ChildGone("the code under test's process ended") from None

    async def ask(self, message: Mapping[str, Any] | None) -> dict[str, Any]:
        """One request and its reply (`None`: only wait for the handshake)."""
        return await asyncio.get_running_loop().run_in_executor(
            self.pool, self._exchange, message
        )

    def stop(self, keep_root: int | None) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        stop_bundle_processes(keep_root)
        for handle in (self.requests, self.replies):
            try:
                handle.close()
            except OSError:
                pass
        self.pool.shutdown(wait=False)


class McpTools:
    """The bundle's tools, called over the child's pipe.

    The harness hands every tool the case inputs, the pass number and (for the check tool) the action's
    outputs. A generated tool declares only its own contract inputs and FastMCP rejects anything else, so
    each call is cut down to the parameters the tool declares."""

    def __init__(self, child: ChildProcess) -> None:
        self.child = child
        self.declared: dict[str, set[str]] | None = None

    async def parameters(self, tool: str) -> set[str] | None:
        if self.declared is None:
            reply = await self.child.ask({"op": "parameters"})
            value = reply.get("value")
            self.declared = (
                {str(k): {str(p) for p in v} for k, v in value.items()}
                if reply.get("ok") is True and isinstance(value, dict)
                else {}
            )
        return self.declared.get(tool)

    async def call(
        self, tool: str, arguments: Mapping[str, JsonValue]
    ) -> dict[str, Any]:
        try:
            allowed = await self.parameters(tool)
            if allowed is not None:
                arguments = {k: v for k, v in arguments.items() if k in allowed}
            reply = await self.child.ask(
                {"op": "call", "tool": tool, "arguments": dict(arguments)}
            )
        except ChildGone as exc:
            raise ToolError(str(exc)) from None
        if reply.get("ok") is not True:
            raise ToolError(str(reply.get("message", "the tool failed"))[:2000])
        return reply.get("value")  # type: ignore[no-any-return]  # None: the harness refuses it


def error_trace(
    case_id: str, category: str, message: str, *, budget: bool = False
) -> HarnessTrace:
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
    if (
        trace.error is not None
        and "MemoryError: the tool exhausted" in trace.error.message
    ):
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
        ui_updates=list(request.harness.ui_updates),
        database_updates=[],
        alerts=[],
        next=None,
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

    @staticmethod
    def case_name(number: int) -> str:
        return f"sbam_case_{number}"

    def case(self, number: int) -> Any:
        """The runner's own connection to the case's database. Its queries (the bundle's fresh-state
        SELECTs among them) get a statement timeout, so a `pg_sleep` cannot hold the runner.
        """
        import psycopg

        name = self.case_name(number)
        self.admin.execute(f'CREATE DATABASE "{name}" TEMPLATE {TEMPLATE_DB}')
        return psycopg.connect(
            host="127.0.0.1",
            user="postgres",
            dbname=name,
            autocommit=True,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        )

    def postmaster_pid(self) -> int | None:
        """The pid of the database's postmaster: the root of the one process tree that is not the
        code under test's."""
        try:
            first = (Path(self.datadir) / "postmaster.pid").read_text().splitlines()[0]
            return int(first)
        except (OSError, IndexError, ValueError):
            return None

    def stop(self) -> None:
        try:
            if self.admin is not None:
                self.admin.close()
            if self.datadir:
                subprocess.run(  # noqa: S603
                    [
                        "pg_ctl",
                        "-D",
                        self.datadir,
                        "stop",
                        "-m",
                        "immediate",
                    ],  # noqa: S607
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
        finally:
            shutil.rmtree(self.datadir, ignore_errors=True)


async def run_case_in_db(
    request: RunnerRequest,
    raw: Mapping[str, Any],
    db: Database,
    number: int,
    bundle_dir: Path,
) -> tuple[HarnessTrace, list[dict[str, Any]]]:
    conn = db.case(number)
    child: ChildProcess | None = None
    case_id = str(raw["case_id"])
    try:
        for statement in raw.get("initial_state") or []:
            try:
                conn.execute(str(statement))
            except Exception as exc:
                raise SeedError(f"{type(exc).__name__}") from None
        try:
            queries = state_queries(bundle_dir, request.tool_module)
        except BundleError as exc:
            return error_trace(case_id, "IMPORT_ERROR", f"BundleError: {exc}"), []
        child = ChildProcess(
            bundle_dir, request.tool_module, db.case_name(number), request.limits
        )
        hello = await asyncio.wait_for(child.ask(None), request.limits.case_timeout_s)
        if (
            hello.get("ok") is not True
        ):  # the bundle did not import (or the channel is broken)
            message = str(hello.get("message", "the bundle did not start"))
            return error_trace(case_id, "IMPORT_ERROR", message), []
        store = PgStore(
            conn,
            request.handoff_table,
            request.harness.key_field,
            raw["input"],
            queries,
            request.harness.handoff_fields,
        )
        spec = load_loop_spec(request.loop_spec)
        config = request.harness
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
                tools=McpTools(child),
                store=store,
                required_inputs=config.required_inputs,
            ),
            request.limits.case_timeout_s,
        )
        child.stop(
            db.postmaster_pid()
        )  # nothing of the code under test runs while the rows are read
        child = None
        try:
            rows = store.rows()[:MAX_DUMPED_ROWS]
        except Exception:
            rows = []
        return as_oom(trace), rows
    except ChildGone as exc:
        return error_trace(case_id, "RUNTIME_ERROR", f"ChildGone: {exc}"), []
    finally:
        if child is not None:
            child.stop(db.postmaster_pid())
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
    slim = [
        c.model_copy(update={"handoff_rows": [], "truncated": True})
        for c in outputs.cases
    ]
    outputs = outputs.model_copy(update={"cases": slim})
    if len(outputs.model_dump_json()) <= limit:
        return outputs
    bare = [
        c.model_copy(update={"trace": {**c.trace, "passes": [], "ui_updates": []}})
        for c in slim
    ]
    return outputs.model_copy(update={"cases": bare})


def write_outputs(path: Path, outputs: RunnerOutputs) -> None:
    """Only called once nothing of the code under test is alive. Whatever it left in `out/` (a forged
    outputs.json, a symlink, a directory in the file's place) is removed first."""
    if (
        path.parent.is_symlink()
    ):  # `rmtree` refuses a symlink; the link itself is what to remove
        path.parent.unlink()
    else:
        shutil.rmtree(path.parent, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(outputs.model_dump_json())
    temp.replace(path)  # the file either exists complete or not at all


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
        except (SystemExit, KeyboardInterrupt) as exc:
            # a tool that tries to end the process: asyncio re-raises these out of the loop, so they
            # are caught here and become a failed case, not a dead runner
            trace = error_trace(
                str(raw["case_id"]),
                "RUNTIME_ERROR",
                f"{type(exc).__name__}: the code under test tried to end the runner",
            )
            rows = []
        results.append(result_of(raw, trace, rows))
    usage, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(
        resource.RUSAGE_CHILDREN
    )
    return RunnerOutputs(
        runtime_digest=runtime_digest(),
        cases=results,
        resources=RunnerResources(
            wall_ms=round((time.monotonic() - started) * 1000),
            cpu_ms=round(
                (
                    usage.ru_utime
                    + usage.ru_stime
                    + children.ru_utime
                    + children.ru_stime
                )
                * 1000
            ),
            peak_rss_kb=int(max(usage.ru_maxrss, children.ru_maxrss)),
        ),
        mode=request.mode,
    )


def main(argv: list[str]) -> int:
    started = time.monotonic()
    harden_runner()
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
        stop_bundle_processes(
            db.postmaster_pid()
        )  # a late writer cannot outlive this line
        out = Path(argv[1]).parent / "out" / "outputs.json"
        write_outputs(out, fit(outputs, request.limits.max_output_bytes))
        return EXIT_OK
    finally:
        db.stop()


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    except (
        BaseException
    ) as exc:  # anything left: end now, never wait for a hung thread at shutdown
        print(f"runner crashed: {type(exc).__name__}", file=sys.stderr)
        code = EXIT_CRASH
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # a hung tool's thread must not keep the VM's process alive
