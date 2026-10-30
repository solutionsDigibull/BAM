"""E13.2 in-VM runner, the parts that need no database and no FastMCP (the container test has them).

`run_cases.py` lives under compose/ because it ships inside the runtime image; it is loaded by path.
The vendored closure is checked in both directions (ADR 0023, decision 7): a module the runner
starts
importing and a listed file it no longer needs both fail here.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sbam_contracts.harness import HarnessError, HarnessTrace
from sbam_contracts.sandbox import RunnerOutputs, RunnerRequest, SandboxLimits, SandboxMode
from sbam_workers.sandbox.inputs import harness_config

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "compose" / "sandbox-runner" / "harness"
DIGEST = "sha256:" + "a" * 64


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return load("sbam_run_cases_under_test", HARNESS / "run_cases.py")


@pytest.fixture(scope="module")
def stage() -> ModuleType:
    return load("sbam_stage_under_test", HARNESS / "stage.py")


def request(**over: Any) -> RunnerRequest:
    base: dict[str, Any] = {
        "limits": SandboxLimits(),
        "mode": SandboxMode.LOCAL_DOCKER_NONCERT,
        "tool_module": "skill.server",
        "loop_spec": {},
        "harness": {
            "action_tool": "act",
            "fixed_check_tool": "check",
            "fresh_state_parameters": ["balance"],
            "required_inputs": ["q"],
            "key_field": "q",
            "handoff_fields": ["iteration_pass", "x"],
            "ui_updates": ["Looping [Pass 1/3]", "Status: Completed"],
        },
        "handoff_table": "handoff",
        "cases": [{"case_id": "c1"}],
    }
    return RunnerRequest.model_validate({**base, **over})


# --- the vendored closure -------------------------------------------------------------------------


def import_closure() -> set[str]:
    """What `python -I run_cases.py` imports from this repository, found by really importing it."""
    code = (
        "import sys, os\n"
        f"sys.path[:0] = [{str(ROOT / 'server' / 'contracts')!r}, {str(ROOT / 'server' /
        'workers')!r},"
        f" {str(HARNESS)!r}]\n"
        "import run_cases\n"
        "root = sys.argv[1]\n"
        "for name, mod in sorted(sys.modules.items()):\n"
        "    f = getattr(mod, '__file__', None)\n"
        "    if f and name.startswith('sbam_') and f.startswith(root):\n"
        "        print(os.path.relpath(f, root))\n"
    )
    out = subprocess.run(  # noqa: S603 - this interpreter, a fixed script, no shell
        [sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def test_e13_2_the_vendored_file_list_is_exactly_the_runners_import_closure(
    stage: ModuleType,
) -> None:
    listed = set(stage.listed())
    closure = import_closure()
    assert closure - listed == set(), "the runner imports files that are not vendored"
    assert listed - closure == set(), "files are vendored that the runner no longer imports"


def test_e13_2_staging_copies_the_closure_and_the_runner_and_nothing_else(
    stage: ModuleType, tmp_path: Path
) -> None:
    copied = stage.stage(ROOT, tmp_path)
    assert "run_cases.py" in copied and "sbam_contracts/harness.py" in copied
    assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.py")} == set(copied)
    assert not any("import rfc8785" in p.read_text() for p in tmp_path.rglob("*.py"))


def test_e13_2_the_staged_tree_imports_without_the_platform_packages(
    stage: ModuleType, tmp_path: Path
) -> None:
    stage.stage(ROOT, tmp_path)
    code = (
        "import sys\n"
        "sys.modules['rfc8785'] = None  # as in the runtime image: not installed\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "import run_cases\n"
        "import sbam_contracts.canonical as c\n"
        "print(c.__file__)\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    # the dev environment has sbam-contracts installed; the staged copy must win over it, and its
    # canonical module must be the stand-in (the real one would have failed on rfc8785 above)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().startswith(str(tmp_path))


def test_e13_2_stand_ins_refuse_to_do_platform_work(stage: ModuleType, tmp_path: Path) -> None:
    stage.stage(ROOT, tmp_path)
    for rel, call in (
        ("sbam_contracts/canonical.py", "canonical_json({})"),
        ("sbam_contracts/canonical.py", "sha256_of({})"),
        ("sbam_contracts/_jsonschema.py", "contract_schema(object, 'x')"),
    ):
        namespace: dict[str, Any] = {}
        exec(compile((tmp_path / rel).read_text(), rel, "exec"), namespace)  # noqa: S102
        with pytest.raises(RuntimeError):
            eval(call, namespace)  # noqa: S307


# --- the harness configuration
# ---------------------------------------------------------------------


PARAMS = {
    "inputs": [
        {"name": "opt", "required": False},
        {"name": "mobile", "required": True},
        {"name": "otp", "required": True},
    ],
    "action_tool": "verify_otp",
    "fixed_check_tool": "check_policy",
    "fresh_state": ["a", "b"],
    "required_fields": ["iteration_pass", "scope"],
}


def test_e13_2_harness_config_is_derived_from_the_render_params() -> None:
    config = harness_config(PARAMS, ["Looping [Pass 1/3]", "Status: Completed"])
    assert (config.action_tool, config.fixed_check_tool) == ("verify_otp", "check_policy")
    assert config.key_field == "mobile"  # the first *required* input, as the template keys it
    assert config.required_inputs == ["mobile", "otp"]
    assert config.fresh_state_parameters == ["a", "b"]
    assert config.handoff_fields == ["iteration_pass", "scope"]


def test_e13_2_a_sub_skill_with_no_required_input_has_no_harness_config() -> None:
    with pytest.raises(ValueError, match="required input"):
        harness_config({**PARAMS, "inputs": [{"name": "opt", "required": False}]}, ["x"])


# --- runner pieces
# ---------------------------------------------------------------------------------


def trace(**over: Any) -> HarnessTrace:
    base: dict[str, Any] = {
        "case_id": "c1",
        "terminal": "SUCCESS",
        "iterations": 1,
        "passes": [],
        "ui_updates": [],
        "handoff_rows": 1,
    }
    return HarnessTrace.model_validate({**base, **over})


def test_e13_2_state_script_step_i_runs_before_pass_i_plus_2(runner: ModuleType) -> None:
    case = runner.loop_case(
        {
            "case_id": "c1",
            "kind": "PASS_AFTER_RETRY",
            "expected_terminal": "SUCCESS",
            "input": {"q": 1},
            "state_script": [["UPDATE t SET a = 1"], ["UPDATE t SET a = 2", "UPDATE t SET b = 3"]],
        },
        request(),
    )
    assert case.state_script == {
        2: {"sql": ["UPDATE t SET a = 1"]},
        3: {"sql": ["UPDATE t SET a = 2", "UPDATE t SET b = 3"]},
    }
    assert (case.action_tool, case.fixed_check_tool) == ("act", "check")


def test_e13_2_a_failed_case_trace_still_validates_with_its_null_terminal(
    runner: ModuleType,
) -> None:
    """`HarnessTrace.to_json_object()` drops `terminal: null`, and the result no longer
    validates."""
    failed = runner.error_trace("c1", "IMPORT_ERROR", "ModuleNotFoundError: x")
    dumped = json.loads(failed.model_dump_json(by_alias=True))
    assert dumped["terminal"] is None
    assert HarnessTrace.model_validate(dumped) == failed
    with pytest.raises(ValueError):  # the pre-existing helper's output is not valid input
        HarnessTrace.model_validate(failed.to_json_object())


def test_e13_2_result_categories_come_from_the_trace(runner: ModuleType) -> None:
    raw = {"case_id": "c1", "expected_terminal": "SUCCESS"}
    ok = runner.result_of(raw, trace(), [{"a": 1}])
    assert ok.passed and ok.failure_category is None and ok.handoff_rows == [{"a": 1}]
    wrong = runner.result_of(raw, trace(terminal="ESCALATED"), [])
    assert not wrong.passed and wrong.failure_category == "ASSERTION_FAILED"
    errored = runner.result_of(raw, runner.error_trace("c1", "TIMEOUT", "slow"), [])
    assert not errored.passed and errored.failure_category == "TIMEOUT"


def test_e13_2_only_the_runners_own_memory_marker_turns_an_error_into_oom(
    runner: ModuleType,
) -> None:
    marked = runner.error_trace(
        "c1", "RUNTIME_ERROR", "ToolError: MemoryError: the tool exhausted its memory"
    )
    assert runner.as_oom(marked).error.category == "OOM"
    plain = runner.error_trace("c1", "RUNTIME_ERROR", "ValueError: MemoryError was mentioned")
    assert runner.as_oom(plain).error.category == "RUNTIME_ERROR"


def test_e13_2_the_memory_marker_reads_the_whole_exception_chain(runner: ModuleType) -> None:
    try:
        try:
            raise MemoryError
        except MemoryError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert runner.memory_error_in(outer)
    assert not runner.memory_error_in(ValueError("x"))
    assert not runner.memory_error_in(None)


def outputs(runner: ModuleType, rows: int = 0, steps: int = 0) -> RunnerOutputs:
    big = {"passes": [{"x": "y" * 500}] * steps, "ui_updates": ["u" * 100] * steps}
    case = runner.result_of(
        {"case_id": "c1", "expected_terminal": "SUCCESS"},
        trace(),
        [{"k": "v" * 200}] * rows,
    )
    case = case.model_copy(update={"trace": {**case.trace, **big}})
    return RunnerOutputs(
        runtime_digest=DIGEST,
        cases=[case],
        resources={"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},  # type: ignore[arg-type]
        mode=SandboxMode.CUBESANDBOX,
    )


def test_e13_2_outputs_that_fit_are_left_alone(runner: ModuleType) -> None:
    out = outputs(runner, rows=2)
    assert runner.fit(out, 10_000_000) == out


def test_e13_2_oversized_outputs_drop_the_dump_first_then_the_pass_detail(
    runner: ModuleType,
) -> None:
    out = outputs(runner, rows=200, steps=100)
    size = len(out.model_dump_json())
    no_dump = runner.fit(out, size - 1000)
    assert no_dump.cases[0].handoff_rows == [] and no_dump.cases[0].truncated
    assert no_dump.cases[0].trace["passes"]  # the evidence that matters is still there
    bare = runner.fit(out, 5000)
    assert bare.cases[0].trace["passes"] == [] and bare.cases[0].trace["ui_updates"] == []
    assert len(bare.model_dump_json()) < size


def test_e13_2_outputs_are_written_atomically(runner: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "out" / "outputs.json"
    runner.write_outputs(target, outputs(runner))
    assert RunnerOutputs.model_validate_json(target.read_text()).cases[0].case_id == "c1"
    assert not list((tmp_path / "out").glob("*.tmp"))  # no half-written sibling is left


class FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> "FakeConn":
        self.executed.append(sql)
        return self

    def fetchall(self) -> list[tuple[dict[str, Any]]]:
        return [(row,) for row in self.rows]


class FakeState:
    def read(self, key: Any) -> Any:
        assert key == "k1"

        class Fresh:
            balance = 5

        return Fresh()


def store(runner: ModuleType, rows: list[dict[str, Any]]) -> Any:
    return runner.PgStore(
        FakeConn(rows), "handoff", "q", {"q": "k1"}, FakeState(), ["iteration_pass", "x"]
    )


def test_e13_2_fresh_state_comes_through_the_bundles_own_reader(runner: ModuleType) -> None:
    assert store(runner, []).read_state(["balance"]) == {"balance": 5}


def test_e13_2_state_script_changes_are_the_cases_own_sql(runner: ModuleType) -> None:
    s = store(runner, [])
    s.apply_script({"sql": ["UPDATE t SET a = 1", "UPDATE t SET b = 2"]})
    assert s.conn.executed == ["UPDATE t SET a = 1", "UPDATE t SET b = 2"]
    s.apply_script({})  # a pass without a step changes nothing


def test_e13_2_the_runner_checks_the_handoff_row_it_never_writes(runner: ModuleType) -> None:
    good = {"iteration_pass": 1, "x": 1}
    s = store(runner, [good])
    s.append_handoff("handoff", {})  # exactly one row, every field: fine
    s.conn.rows.append({"iteration_pass": 2, "x": 2})
    s.append_handoff("handoff", {})  # pass 2 -> two rows in total: still exactly one new row


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ([], "left 0 handoff rows"),
        ([{"iteration_pass": 1, "x": 1}] * 2, "left 2 handoff rows"),
        ([{"iteration_pass": 1}], "misses required fields"),
    ],
)
def test_e13_2_a_skipped_doubled_or_incomplete_append_fails_the_pass(
    runner: ModuleType, rows: list[dict[str, Any]], why: str
) -> None:
    with pytest.raises(runner.HandoffError, match=why):
        store(runner, rows).append_handoff("handoff", {})


def test_e13_2_the_digest_comes_from_the_vm_file_then_the_environment(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "RUNTIME_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("SANDBOX_RUNTIME_DIGEST", "b" * 64)
    assert runner.runtime_digest() == "sha256:" + "b" * 64
    file = tmp_path / "sbam-runtime"
    file.write_text("sha256:" + "c" * 64 + "\n")
    monkeypatch.setattr(runner, "RUNTIME_FILE", str(file))
    assert runner.runtime_digest() == "sha256:" + "c" * 64
    file.write_text("garbage")
    assert runner.runtime_digest() == "sha256:" + "b" * 64  # a bad file falls through, never passes
    monkeypatch.delenv("SANDBOX_RUNTIME_DIGEST")
    with pytest.raises(RuntimeError):
        runner.runtime_digest()


def test_e13_2_a_request_the_runner_cannot_read_exits_2_without_outputs(
    runner: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "request.json").write_text("{not json")
    assert runner.main(["run_cases.py", str(tmp_path / "request.json")]) == runner.EXIT_REQUEST
    assert runner.main(["run_cases.py"]) == runner.EXIT_REQUEST
    assert not (tmp_path / "out").exists()


def test_e13_2_exit_codes_match_what_the_classifier_reads(runner: ModuleType) -> None:
    from sbam_workers.sandbox import classify

    assert (runner.EXIT_OK, runner.EXIT_DATABASE, runner.EXIT_SEED) == (
        classify.EXIT_OK,
        classify.EXIT_DATABASE,
        classify.EXIT_SEED,
    )
    assert runner.HarnessError is HarnessError  # the runner builds the contract's own error type
