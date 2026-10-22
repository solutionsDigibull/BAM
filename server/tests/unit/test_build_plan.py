"""E10.3 build plan + scheduler, without Temporal: the parallel limit config, graph validation and
the scheduling decisions. The workflow-level tests are tests/temporal/test_build_scheduler.py."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.pipeline import BuildPlan
from sbam_workers.build.plan import BuildSettings, find_cycle, make_plan
from sbam_workers.pipeline.build_scheduler import BuildScheduler
from temporalio.exceptions import ApplicationError


def skills(n: int) -> list[uuid.UUID]:
    return sorted((uuid.uuid4() for _ in range(n)), key=str)


# --- T05: config validation ----------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 17])
def test_e10_3_t05_invalid_max_parallel_rejected(value: int, monkeypatch: Any) -> None:
    with pytest.raises(ValidationError):
        BuildPlan(max_parallel=value)
    monkeypatch.setenv("BUILD_MAX_PARALLEL", str(value))
    with pytest.raises(ValidationError):
        BuildSettings()


def test_e10_3_t05_config_bounds_and_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("BUILD_MAX_PARALLEL", raising=False)
    assert BuildSettings(_env_file=None).max_parallel == 4  # type: ignore[call-arg]
    monkeypatch.setenv("BUILD_MAX_PARALLEL", "16")
    assert BuildSettings().max_parallel == 16


def test_e10_3_unknown_ids_and_cycles_rejected() -> None:
    a, b, c = skills(3)
    with pytest.raises(ApplicationError) as err:
        make_plan([a, b], {a: [c]}, 4)
    assert (err.value.type, err.value.non_retryable) == ("UnknownSubSkill", True)
    with pytest.raises(ApplicationError) as err:
        make_plan([a, b, c], {a: [b], b: [c], c: [a]}, 4)
    assert (err.value.type, err.value.non_retryable) == ("DependencyCycle", True)
    assert find_cycle({a: [a]}) == [a, a]
    assert make_plan([a, b, c], {c: [a, b], b: []}, 2) == BuildPlan(
        max_parallel=2, depends_on={c: sorted([a, b], key=str)}
    )


def test_e10_3_scheduler_blocks_a_whole_chain() -> None:
    a, b, c, d = skills(4)
    scheduler = BuildScheduler([a, b, c, d], {b: [a], c: [b]}, 4, certified=[])
    assert scheduler.start_ready() == [a, d]
    scheduler.finished(a, certified=False)
    assert scheduler.start_ready() == []
    assert scheduler.blocked == [b, c]
    scheduler.finished(d, certified=True)
    assert scheduler.needs_review() == [a, b, c]


def test_e10_3_scheduler_upstream_outside_the_round() -> None:
    a, b, c = skills(3)
    deps = {b: [a], c: [a]}
    assert BuildScheduler([b], deps, 4, certified=[a]).start_ready() == [b]  # certified earlier
    later = BuildScheduler([c], deps, 4, certified=[])  # upstream neither certified nor rebuilt
    assert later.start_ready() == []
    assert later.blocked == [c]
