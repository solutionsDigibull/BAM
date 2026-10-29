"""#476: the CI guard that fails when an opt-in test module skips instead of running."""

import importlib.util
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("ci_assert_ran", REPO / "scripts/ci_assert_ran.py")
assert _spec and _spec.loader
guard: ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def report(tmp_path: Path, *cases: str) -> str:
    path = tmp_path / "junit.xml"
    path.write_text(f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>")
    return str(path)


def case(module: str, name: str, skipped: bool = False) -> str:
    body = '<skipped message="gated"/>' if skipped else ""
    return f'<testcase classname="tests.unit.{module}" name="{name}">{body}</testcase>'


def test_e1_ci_t01_a_module_that_ran_passes(tmp_path: Path) -> None:
    path = report(tmp_path, case("test_a", "t1"), case("test_other", "t2", skipped=True))
    assert guard.check(path, ["test_a"]) == []


def test_e1_ci_t02_a_skipped_test_is_named(tmp_path: Path) -> None:
    path = report(tmp_path, case("test_a", "t1"), case("test_a", "t2", skipped=True))
    assert guard.check(path, ["test_a"]) == ["test_a: t2 was skipped"]


def test_e1_ci_t03_a_module_that_never_ran_fails(tmp_path: Path) -> None:
    assert guard.check(report(tmp_path, case("test_a", "t1")), ["test_b"]) == [
        "test_b: no test ran"
    ]


def test_e1_ci_t04_a_class_based_test_counts_for_its_module(tmp_path: Path) -> None:
    cls = '<testcase classname="tests.unit.test_a.TestX" name="t1"><skipped/></testcase>'
    assert guard.check(report(tmp_path, cls), ["test_a"]) == ["test_a: t1 was skipped"]
