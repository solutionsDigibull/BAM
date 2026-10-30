"""E15.5: the test-generation golden set, its scoring and its gate (no model is called)."""

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from sbam_api.testgen.validate import input_schema
from sbam_contracts.testgen import TestgenOutput
from sbam_workers.evals.baseline import Verdict, load_baseline, load_gate_config
from sbam_workers.evals.gate import (
    EXIT,
    GateReport,
    TestgenGenerator,
    run_testgen,
    run_testgen_gate,
)
from sbam_workers.evals.golden import (
    EVALS_DIR,
    GOLDEN_DIR,
    Task,
    build_testgen_isatvon,
    check_golden,
    load_testgen,
)
from sbam_workers.evals.golden import (
    testgen_base as load_base,
)
from sbam_workers.evals.scoring import TESTGEN_CHECKS, score_testgen
from sbam_workers.evals.testgen_set import BOUNDS, DOMAINS, render_json
from sbam_workers.llm.client import LlmTransportError
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.test.testgen import allowed_kinds

SET = load_testgen()
BASE = load_base()
INPUTS = ["customer_id", "amount"]


def output(inputs: list[str]) -> TestgenOutput:
    """A valid answer whose cases use every name in `inputs`."""
    body = {name: "x" for name in inputs}
    kinds = (("PASS_FIRST", "SUCCESS"), ("ESCALATION", "ESCALATED"), ("NO_OP", "NO_OP"))
    return TestgenOutput.model_validate(
        {
            "cases": [
                {
                    "name": f"{kind.lower()} case",
                    "kind": kind,
                    "input": body,
                    "expected_terminal": t,
                }
                for kind, t in kinds
            ]
        }
    )


# --- the set --------------------------------------------------------------------------------


def test_e15_5_the_committed_testgen_set_is_what_the_generator_writes() -> None:
    assert (GOLDEN_DIR / "testgen.json").read_text(encoding="utf-8") == render_json()


def test_e15_5_the_testgen_set_has_twenty_valid_distinct_sub_skills() -> None:
    assert check_golden() == []
    assert len({i.id for i in SET.items}) == len(SET.items) == len(DOMAINS) * len(BOUNDS) == 20
    assert all(i.synthetic is True for i in SET.items)
    inputs = {tuple(SET.domains[i.domain].required_inputs) for i in SET.items}
    assert len(inputs) == len(DOMAINS)  # five different closed input schemas
    for item in SET.items:
        assert build_testgen_isatvon(item, BASE, SET.domains)[
            "isatvon_definition"
        ]  # valid, or it raises


def test_e15_5_each_variant_carries_its_own_inputs_tables_and_bound() -> None:
    for item in SET.items:
        domain = SET.domains[item.domain]
        isatvon = build_testgen_isatvon(item, BASE, SET.domains)
        schema = input_schema(isatvon)
        assert schema["required"] == domain.required_inputs
        assert schema["additionalProperties"] is False
        v = isatvon["isatvon_definition"]["v_variables"]
        assert v["max_iterations"] == item.max_iterations
        assert v["tables"] == domain.tables


def test_e15_5_a_bound_of_one_takes_pass_after_retry_out_of_the_allowed_kinds() -> None:
    for item in SET.items:
        kinds = {k.value for k in allowed_kinds(build_testgen_isatvon(item, BASE, SET.domains))}
        assert ("PASS_AFTER_RETRY" in kinds) is (item.max_iterations > 1)


def test_e15_5_the_base_is_not_changed_by_building_a_variant() -> None:
    before = json.dumps(BASE, sort_keys=True)
    build_testgen_isatvon(SET.items[5], BASE, SET.domains)
    assert json.dumps(BASE, sort_keys=True) == before


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


LOGIN = "login with a one-time code"
LOGIN_WORDS = ("investor", "otp", "login", "jwt", "rbac", "device", "mobile", "vault", "kyc")


def test_e15_5_no_login_text_is_left_in_the_other_domains() -> None:
    """The first version only swapped the tables and inputs of the login sample; its prose still
    named `investor_profiles`, and the model wrote its seed SQL into that table, which the Sub-Skill
    did not declare. Every other domain must read as its own process, start to finish."""
    others = [i for i in SET.items if i.domain != LOGIN]
    assert len(others) == 16
    for item in others:
        text = " ".join(_strings(build_testgen_isatvon(item, BASE, SET.domains))).lower()
        leaked = [w for w in LOGIN_WORDS if w in text]
        assert not leaked, f"{item.id} ({item.domain}) still mentions {leaked}"


def test_e15_5_each_document_names_only_its_own_tables() -> None:
    for item in SET.items:
        text = " ".join(_strings(build_testgen_isatvon(item, BASE, SET.domains)))
        mine = set(SET.domains[item.domain].tables)
        others = {t for name, d in SET.domains.items() if name != item.domain for t in d.tables}
        assert all(t in text for t in mine), f"{item.id} does not mention its own tables"
        assert not [
            t for t in others - mine if t in text
        ], f"{item.id} mentions another domain's table"


def test_e15_5_the_handoff_table_is_a_declared_table_and_the_conditions_match_the_state() -> None:
    for item in SET.items:
        d = build_testgen_isatvon(item, BASE, SET.domains)["isatvon_definition"]
        assert d["o_outcome"]["state_handoff_file"]["table"] in d["v_variables"]["tables"]
        escalation = d["v_variables"]["escalation_condition"]
        assert any(p in escalation for p in d["s_source"]["fresh_state_parameters"])


def test_e15_5_a_variant_that_is_not_a_valid_sub_skill_is_a_problem(tmp_path: Path) -> None:
    for name in ("tasks.json", "ocr.json", "testgen.json"):
        (tmp_path / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "testgen-base.json").write_text("{}", encoding="utf-8")  # no definition to override
    problems = check_golden(tmp_path)
    assert sum("not a valid Sub-Skill" in p for p in problems) == 20


# --- scoring --------------------------------------------------------------------------------


def test_e15_5_a_clean_answer_that_uses_every_input_scores_one() -> None:
    result = score_testgen("testgen-01", INPUTS, output(INPUTS), attempts=1)
    assert (result.found, result.total, result.missing) == (3, 3, ())
    assert TESTGEN_CHECKS == ("valid", "clean", "covers_inputs")


def test_e15_5_an_answer_that_needed_a_re_ask_loses_the_clean_check() -> None:
    result = score_testgen("testgen-01", INPUTS, output(INPUTS), attempts=2)
    assert result.missing == ("clean",) and result.score == pytest.approx(2 / 3)


def test_e15_5_an_answer_that_never_validated_scores_zero() -> None:
    result = score_testgen("testgen-01", INPUTS, None, attempts=4)
    assert result.missing == TESTGEN_CHECKS and result.score == 0.0


def test_e15_5_cases_that_skip_a_required_input_lose_the_coverage_check() -> None:
    result = score_testgen("testgen-01", INPUTS, output(["customer_id"]), attempts=1)
    assert result.missing == ("covers_inputs",)


# --- the gate -------------------------------------------------------------------------------


def generator_of(attempts: int = 1, valid: bool = True) -> Callable[[], TestgenGenerator]:
    def make() -> TestgenGenerator:
        async def generate(isatvon: Mapping[str, Any]) -> tuple[TestgenOutput | None, int]:
            required = isatvon["isatvon_definition"]["s_source"]["required_inputs"]
            return (output(required) if valid else None), attempts

        return generate

    return make


def sandbox(tmp_path: Path) -> dict[str, Path]:
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    for name in ("tasks.json", "ocr.json", "testgen.json", "testgen-base.json"):
        (golden / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "gate.json").write_text((EVALS_DIR / "gate.json").read_text(encoding="utf-8"))
    return {"golden_dir": golden, "evals_dir": tmp_path, "baseline_dir": tmp_path / "baselines"}


def run(factory: Callable[[], TestgenGenerator], tmp_path: Path, **kwargs: Any) -> GateReport:
    return asyncio.run(run_testgen_gate(factory, **sandbox(tmp_path), **kwargs))  # type: ignore[arg-type]


def test_e15_5_recording_a_testgen_baseline_pins_the_testgen_prompt(tmp_path: Path) -> None:
    report = run(generator_of(), tmp_path, record=True, runs=1)
    written = json.loads((tmp_path / "baselines" / "testgen.json").read_text(encoding="utf-8"))
    assert report.decision.verdict is Verdict.PASS and report.score == 1.0
    assert written["task"] == "testgen" and written["alias"] == "sbam-testgen"
    newest = PromptRegistry().get("testgen")  # the version the baseline must pin
    assert written["items"] == 20
    assert written["prompt_sha256"] == {f"testgen/{newest.version}": newest.sha256}
    assert not (tmp_path / "baselines" / "ocr.json").exists()  # one task's baseline only


def test_e15_5_a_clean_run_passes_and_a_worse_prompt_blocks_the_change(tmp_path: Path) -> None:
    run(generator_of(), tmp_path, record=True, runs=1)
    assert run(generator_of(), tmp_path, runs=1).decision.verdict is Verdict.PASS
    needs_retries = run(generator_of(attempts=3), tmp_path, runs=1)  # valid, but never clean
    assert needs_retries.decision.verdict is Verdict.REGRESSED
    assert needs_retries.score == pytest.approx(2 / 3) and EXIT[Verdict.REGRESSED] == 1
    never_valid = run(generator_of(attempts=4, valid=False), tmp_path, runs=1)
    assert never_valid.decision.verdict is Verdict.REGRESSED and never_valid.score == 0.0


def test_e15_5_a_provider_outage_in_testgen_is_an_infra_error_never_a_pass(tmp_path: Path) -> None:
    run(generator_of(), tmp_path, record=True, runs=1)

    def down() -> TestgenGenerator:
        async def generate(isatvon: Mapping[str, Any]) -> tuple[TestgenOutput | None, int]:
            raise LlmTransportError("bifrost returned 503")

        return generate

    report = run(down, tmp_path, runs=1)
    assert report.decision.verdict is Verdict.INFRA_ERROR and report.score is None


def test_e15_5_each_testgen_run_gets_its_own_generator() -> None:
    made = []

    def factory() -> TestgenGenerator:
        made.append(1)
        return generator_of()()

    results = asyncio.run(run_testgen(factory, SET.items, BASE, SET.domains, runs=3))
    assert len(made) == 3 and [len(r) for r in results] == [20, 20, 20]
    assert all(r.score == 1.0 for run_results in results for r in run_results)


def test_e15_5_testgen_is_listed_as_a_ready_task() -> None:
    from sbam_workers.evals.golden import load_tasks

    ready = sorted(t.task for t in load_tasks() if t.status == "ready")
    assert ready == sorted([Task.OCR, Task.TESTGEN])


def test_e15_5_the_committed_testgen_baseline_is_valid_and_matches_the_set() -> None:
    committed = load_baseline(Task.TESTGEN)
    assert committed is not None  # the gate has something to compare with
    assert committed.items == len(SET.items) and committed.alias == "sbam-testgen"
    assert committed.runs == load_gate_config().runs
    assert 0.0 <= committed.score <= 1.0
    assert any(key.startswith("testgen/") for key in committed.prompt_sha256)
