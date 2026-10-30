"""E15.5: the code-generation golden set, its scoring and its gate (no model is called)."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import ms01_params
from fixtures import fixture_path
from sbam_contracts.isatvon14 import SubSkill, build_contracts
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen.bodies import CodeBodies, GeneratedBodies
from sbam_workers.build.codegen.params import params_from_manifest, params_from_parts
from sbam_workers.evals.baseline import Verdict, load_baseline, load_gate_config
from sbam_workers.evals.codegen_set import (
    CodegenCase,
    build_cases,
    render_json,
    verify_items,
)
from sbam_workers.evals.gate import (
    EXIT,
    CodegenRunner,
    GateReport,
    run_codegen,
    run_codegen_gate,
)
from sbam_workers.evals.golden import (
    EVALS_DIR,
    GOLDEN_DIR,
    Task,
    check_golden,
    load_codegen,
    load_tasks,
    load_testgen,
)
from sbam_workers.evals.golden import (
    testgen_base as load_base,
)
from sbam_workers.evals.scoring import CODEGEN_CHECKS, score_codegen
from sbam_workers.evals.testgen_set import BOUNDS
from sbam_workers.llm.client import LlmTransportError
from sbam_workers.llm.registry import PromptRegistry

SET = load_codegen()
SHARED = load_testgen()
BASE = load_base()
CASES = build_cases(SET.items, BASE, SHARED.domains)


def bodies_returning(names: list[str]) -> CodeBodies:
    returned = ", ".join(f'"{n}": True' for n in names)
    return CodeBodies.model_validate(
        {
            "action_body": "return {}\n",
            "check_body": f"return {{{returned}}}\n",
            "state_queries": {},
            "helpers": "",
        }
    )


def generated(
    names: list[str],
    findings: tuple[Any, ...] = (),
    files: dict[str, str] | None = None,
    attempts: int = 1,
) -> GeneratedBodies:
    return GeneratedBodies(
        bodies=bodies_returning(names),
        findings=findings,
        files={"action_tool.py": "x"} if files is None else files,
        allowlist=(),
        llm_call_id=None,
        trace_id="t",
        attempts=attempts,
        cached=False,
    )


# --- the set --------------------------------------------------------------------------------


def test_e15_5_the_committed_codegen_set_is_what_the_generator_writes() -> None:
    assert (GOLDEN_DIR / "codegen.json").read_text(encoding="utf-8") == render_json()


def test_e15_5_the_codegen_set_is_sound_and_shares_the_testgen_sub_skills() -> None:
    assert check_golden() == []
    assert len({i.id for i in SET.items}) == len(SET.items) == 20
    assert {i.domain for i in SET.items} == set(SHARED.domains)
    assert {i.max_iterations for i in SET.items} == set(BOUNDS)
    assert all(i.synthetic is True for i in SET.items)


def test_e15_5_every_item_gives_params_the_template_accepts_and_a_skeleton() -> None:
    assert verify_items(SET.items, BASE, SHARED.domains) == []
    for case in CASES:
        profile = SHARED.domains[case.item.domain]
        assert case.params["fresh_state"] == profile.fresh_state_parameters
        assert case.params["handoff_table"] == profile.tables[1]
        assert case.skeleton.files, case.item.id
        assert "null" not in case.params["gate_outputs"]


def test_e15_5_a_gate_expression_the_template_cannot_use_is_a_problem() -> None:
    """`is not null` once made `null` a gate output the check tool had to return."""
    name = next(iter(SHARED.domains))
    odd = SHARED.domains[name].model_copy(update={"success_gate": "x is not null AND y == True"})
    item = next(i for i in SET.items if i.domain == name)
    problems = verify_items([item], BASE, {**SHARED.domains, name: odd})
    assert len(problems) == 1 and "odd gate outputs" in problems[0]


def test_e15_5_params_from_parts_gives_what_params_from_manifest_gave() -> None:
    manifest = SkillManifest.model_validate(
        json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8"))
    )
    data = manifest.dump()
    again = params_from_parts(
        data["skill_metadata"]["skill_id"],
        data["sbam_build_record"]["isatvon_detail"],
        data["contracts"],
    )
    assert again == params_from_manifest(manifest) == ms01_params()
    # and a Sub-Skill's own contracts give the same shape for a Sub-Skill with no manifest
    sub = SubSkill.model_validate(
        json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    )
    assert set(
        params_from_parts(
            "MS01.1", sub.isatvon_definition.model_dump(mode="json"), build_contracts(sub)
        )
    ) == set(again)


# --- scoring --------------------------------------------------------------------------------

GATE = ["otp_match", "policy_violation"]


def test_e15_5_clean_assembled_bodies_that_name_every_gate_output_score_four_of_four() -> None:
    result = score_codegen("codegen-01", generated(GATE), GATE)
    assert (result.found, result.total, result.missing) == (4, 4, ())
    assert CODEGEN_CHECKS == ("valid", "accepted", "clean", "complete")


def test_e15_5_no_answer_scores_zero() -> None:
    result = score_codegen("codegen-01", None, GATE)
    assert result.score == 0.0 and result.missing == CODEGEN_CHECKS


def test_e15_5_bodies_the_pre_check_refuses_or_that_did_not_assemble_lose_accepted() -> None:
    refused = score_codegen("codegen-01", generated(GATE, findings=("MANUAL_LOOP",)), GATE)
    unassembled = score_codegen("codegen-01", generated(GATE, files={}), GATE)
    assert refused.missing == ("accepted",) and unassembled.missing == ("accepted",)


def test_e15_5_a_re_ask_loses_clean_and_a_missing_gate_output_loses_complete() -> None:
    assert score_codegen("codegen-01", generated(GATE, attempts=2), GATE).missing == ("clean",)
    partial = score_codegen("codegen-01", generated(["otp_match"]), GATE)
    assert partial.missing == ("complete",)
    quoted = score_codegen("codegen-01", generated(GATE), [*GATE, "extra_output"])
    assert quoted.missing == ("complete",)


# --- the gate -------------------------------------------------------------------------------


def runner_of(
    answer: Callable[[CodegenCase], GeneratedBodies | None],
) -> Callable[[], CodegenRunner]:
    def make() -> CodegenRunner:
        async def run(case: CodegenCase) -> GeneratedBodies | None:
            return answer(case)

        return run

    return make


def perfect(case: CodegenCase) -> GeneratedBodies:
    return generated(list(case.params["gate_outputs"]))


def sandbox(tmp_path: Path) -> dict[str, Path]:
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    for name in ("tasks.json", "codegen.json", "testgen.json", "testgen-base.json"):
        (golden / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "gate.json").write_text((EVALS_DIR / "gate.json").read_text(encoding="utf-8"))
    return {"golden_dir": golden, "evals_dir": tmp_path, "baseline_dir": tmp_path / "baselines"}


def run(factory: Callable[[], CodegenRunner], tmp_path: Path, **kwargs: Any) -> GateReport:
    return asyncio.run(run_codegen_gate(factory, **sandbox(tmp_path), **kwargs))  # type: ignore[arg-type]


def test_e15_5_recording_a_codegen_baseline_pins_the_codegen_prompt(tmp_path: Path) -> None:
    report = run(runner_of(perfect), tmp_path, record=True, runs=1)
    written = json.loads((tmp_path / "baselines" / "codegen.json").read_text(encoding="utf-8"))
    newest = PromptRegistry().get("codegen-bodies")
    assert report.decision.verdict is Verdict.PASS and report.score == 1.0
    assert written["task"] == "codegen" and written["alias"] == "sbam-codegen"
    assert written["items"] == 20
    assert written["prompt_sha256"] == {f"codegen-bodies/{newest.version}": newest.sha256}


def test_e15_5_good_generation_passes_and_bad_generation_is_blocked(tmp_path: Path) -> None:
    run(runner_of(perfect), tmp_path, record=True, runs=1)
    assert run(runner_of(perfect), tmp_path, runs=1).decision.verdict is Verdict.PASS
    refused = run(
        runner_of(lambda c: generated(list(c.params["gate_outputs"]), findings=("x",))),
        tmp_path,
        runs=1,
    )
    assert refused.decision.verdict is Verdict.REGRESSED and EXIT[Verdict.REGRESSED] == 1
    silent = run(runner_of(lambda _: None), tmp_path, runs=1)
    assert silent.decision.verdict is Verdict.REGRESSED and silent.score == 0.0


def test_e15_5_a_provider_outage_in_codegen_is_an_infra_error_never_a_pass(tmp_path: Path) -> None:
    run(runner_of(perfect), tmp_path, record=True, runs=1)

    def down() -> CodegenRunner:
        async def run_(case: CodegenCase) -> GeneratedBodies | None:
            raise LlmTransportError("bifrost returned 503")

        return run_

    report = run(down, tmp_path, runs=1)
    assert report.decision.verdict is Verdict.INFRA_ERROR and report.score is None


def test_e15_5_each_codegen_run_gets_its_own_runner() -> None:
    made = []

    def factory() -> CodegenRunner:
        made.append(1)
        return runner_of(perfect)()

    results = asyncio.run(run_codegen(factory, CASES, runs=3))
    assert len(made) == 3 and [len(r) for r in results] == [20, 20, 20]
    assert all(r.score == 1.0 for run_results in results for r in run_results)


def test_e15_5_codegen_is_a_ready_task() -> None:
    assert Task.CODEGEN in {t.task for t in load_tasks() if t.status == "ready"}


@pytest.mark.parametrize("bound", BOUNDS)
def test_e15_5_each_iteration_bound_is_in_the_set(bound: int) -> None:
    assert sum(1 for i in SET.items if i.max_iterations == bound) == 5


def test_e15_5_the_committed_codegen_baseline_is_valid_and_matches_the_set() -> None:
    committed = load_baseline(Task.CODEGEN)
    assert committed is not None  # the gate has something to compare with
    assert committed.items == len(SET.items) and committed.alias == "sbam-codegen"
    assert committed.runs == load_gate_config().runs
    assert 0.0 <= committed.score <= 1.0
    assert any(key.startswith("codegen-bodies/") for key in committed.prompt_sha256)
