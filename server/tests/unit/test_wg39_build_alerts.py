"""WG-39 (#926): the stuck-stage and cost-over-threshold alerts, and the two emitters behind them.
The rules are checked as files (compose/grafana); the idle gauge's query and the cost counter run
on SQLite and a fake counter, never on a real collector."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pipeline_db import pipeline_sessions
from sbam_api.core.ids import uuid7
from sbam_api.temporal.models import TemporalRun
from sbam_api.temporal.repository import longest_idle_build_seconds
from sbam_workers.llm import spend_metrics
from test_grafana_provisioning import minutes, rule, rule_expr, threshold

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def run_row(
    *, stage: str | None, age_min: int, state: str = "RUNNING", kind: str = "sub_skill"
) -> Any:
    return TemporalRun(
        id=uuid7(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        temporal_workflow_id=f"build-{uuid.uuid4()}-1",
        temporal_run_id="r1",
        type="SubSkillBuild",
        task_queue="build",
        subject_type=kind,
        subject_id=uuid.uuid4(),
        state=state,
        current_stage=stage,
        updated_at=NOW - timedelta(minutes=age_min),
    )


def idle(*rows: Any) -> float:
    sessions = pipeline_sessions()
    with sessions() as s:
        s.add_all(rows)
        s.commit()
        return longest_idle_build_seconds(s, NOW)


def test_wg39_t30_the_stuck_stage_rule_reads_the_idle_gauge_at_30_minutes() -> None:
    r = rule("sbam-build-stage-stuck")
    assert threshold(r) == ("gt", 1800) and "sbam_build_idle_seconds" in rule_expr(r)
    assert minutes(r["for"]) == 5 and r["noDataState"] == "OK"


def test_wg39_t31_the_cost_rule_reads_one_hour_of_recorded_spend() -> None:
    r = rule("sbam-llm-cost-per-hour")
    assert threshold(r) == ("gt", 5)
    assert "increase(sbam_llm_cost_usd_total[1h])" in rule_expr(r)


def test_wg39_t32_the_gauge_is_the_age_of_the_quietest_running_build() -> None:
    assert (
        idle(run_row(stage="generate_bodies", age_min=10), run_row(stage="assemble", age_min=45))
        == 45 * 60
    )


def test_wg39_t33_no_running_build_reads_zero() -> None:
    assert idle() == 0.0
    assert idle(run_row(stage="done", age_min=90, state="COMPLETED")) == 0.0


def test_wg39_t34_a_build_waiting_for_a_person_is_not_stuck() -> None:
    waiting = (
        run_row(stage="wait_repo_choice", age_min=72 * 60),
        run_row(stage="wait_retry", age_min=600),
        run_row(stage="generate_bodies", age_min=3),
    )
    assert idle(*waiting) == 3 * 60


def test_wg39_t35_only_sub_skill_builds_count() -> None:
    assert idle(run_row(stage="sow", age_min=500, kind="project")) == 0.0


class FakeCounter:
    def __init__(self) -> None:
        self.added: list[tuple[float, dict[str, str]]] = []

    def add(self, amount: float, attributes: dict[str, str]) -> None:
        self.added.append((amount, attributes))


def test_wg39_t36_the_cost_counter_records_positive_spend_by_alias_only(monkeypatch: Any) -> None:
    fake = FakeCounter()
    monkeypatch.setattr(spend_metrics, "_counter", fake)
    spend_metrics.record("sbam-codegen", Decimal("0.0123"))
    spend_metrics.record("sbam-codegen", Decimal(0))  # a free call adds nothing
    assert fake.added == [(0.0123, {"alias": "sbam-codegen"})]
