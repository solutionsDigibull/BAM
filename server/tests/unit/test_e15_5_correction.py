"""E15.5: the correction golden set, its scoring and its gate (no model is called)."""

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.build import IsatvonPatchClass
from sbam_workers.build.codegen.bodies import CodeBodies
from sbam_workers.build.codegen.correction import Correction, CorrectorOutput
from sbam_workers.evals.baseline import Verdict, load_baseline, load_gate_config
from sbam_workers.evals.correction_set import (
    DEFECTS,
    MUST_KEEP,
    base_from_sources,
    build_env,
    findings_of,
    flawed_bodies,
    render_base,
    render_json,
    verify_items,
)
from sbam_workers.evals.gate import (
    EXIT,
    CorrectionRunner,
    GateReport,
    run_correction,
    run_correction_gate,
)
from sbam_workers.evals.golden import (
    EVALS_DIR,
    GOLDEN_DIR,
    Task,
    apply_defect,
    check_golden,
    correction_base,
    load_correction,
    load_tasks,
)
from sbam_workers.evals.scoring import CORRECTION_CHECKS, score_correction
from sbam_workers.llm.client import LlmTransportError
from sbam_workers.llm.registry import PromptRegistry

SET = load_correction()
BASE = correction_base()
ENV = build_env(BASE)
CLEAN = CodeBodies.model_validate(BASE["bodies"])


def correction(
    bodies: CodeBodies,
    findings: tuple[Any, ...] = (),
    patch_class: IsatvonPatchClass = IsatvonPatchClass.NONE,
) -> Correction:
    return Correction(
        output=CorrectorOutput(rationale="x"),
        bodies=bodies,
        sub_skill=None,
        isatvon_sha256=None,
        loop_spec=None,
        findings=findings,
        llm_call_id=None,
        trace_id="t",
        attempts=1,
        cached=False,
        patch_class=patch_class,
    )


# --- the set --------------------------------------------------------------------------------


def test_e15_5_the_committed_correction_files_are_what_the_generator_writes() -> None:
    assert (GOLDEN_DIR / "correction.json").read_text(encoding="utf-8") == render_json()
    assert (GOLDEN_DIR / "correction-base.json").read_text(encoding="utf-8") == render_base()


def test_e15_5_the_base_is_a_copy_of_the_repo_fixtures() -> None:
    assert BASE == base_from_sources()  # a drifted fixture means the baseline no longer applies


def test_e15_5_the_correction_set_is_sound_and_every_flaw_raises_its_code() -> None:
    assert check_golden() == []
    assert verify_items(SET.items, BASE) == []
    assert len({i.id for i in SET.items}) == len(SET.items) == len(DEFECTS) == 20


def test_e15_5_five_kinds_of_flaw_four_variants_each() -> None:
    kinds: dict[str, int] = {}
    for item in SET.items:
        kinds[item.expect_code] = kinds.get(item.expect_code, 0) + 1
    assert kinds == {
        "MANUAL_LOOP": 4,
        "FORBIDDEN_IMPORT": 4,
        "FORBIDDEN_CALL": 4,
        "SQL_UNSAFE": 4,
        "IMPORT_NOT_ALLOWED": 4,
    }
    assert all(i.synthetic is True for i in SET.items)


def test_e15_5_the_unflawed_bodies_are_clean_and_each_flaw_changes_one_slot_only() -> None:
    assert findings_of(CLEAN, ENV) == []
    for item in SET.items:
        flawed = flawed_bodies(item, BASE).model_dump()
        changed = [slot for slot, value in flawed.items() if value != BASE["bodies"][slot]]
        assert changed == [item.defect.slot], item.id


def test_e15_5_the_findings_are_the_pre_checks_own() -> None:
    for item in SET.items:
        findings = findings_of(flawed_bodies(item, BASE), ENV)
        assert item.expect_code in {f["code"] for f in findings}, item.id
        assert all(set(f) == {"gate", "code", "message", "field"} for f in findings)


def test_e15_5_a_defect_whose_text_is_not_there_exactly_once_is_refused() -> None:
    item = next(i for i in SET.items if i.defect.find is not None)
    broken = item.model_copy(
        update={"defect": item.defect.model_copy(update={"find": "no such text"})}
    )
    with pytest.raises(ValueError, match="exactly once"):
        apply_defect(broken, BASE["bodies"])


def test_e15_5_a_set_with_a_flaw_that_raises_the_wrong_code_is_a_problem() -> None:
    wrong = SET.items[0].model_copy(update={"expect_code": "SQL_UNSAFE"})
    problems = verify_items([wrong], BASE)
    assert len(problems) == 1 and "not SQL_UNSAFE" in problems[0]


def test_e15_5_the_must_keep_tokens_are_in_the_unflawed_bodies() -> None:
    text = json.dumps(BASE["bodies"])
    assert all(token in text for token in MUST_KEEP)


# --- scoring --------------------------------------------------------------------------------


def one() -> tuple[Any, dict[str, Any]]:
    item = SET.items[0]  # a while loop in the check body
    return item, flawed_bodies(item, BASE).model_dump()


def test_e15_5_a_correct_repair_scores_four_of_four() -> None:
    item, flawed = one()
    result = score_correction(item.id, flawed, item.defect.slot, correction(CLEAN), MUST_KEEP)
    assert (result.found, result.total, result.missing) == (4, 4, ())
    assert CORRECTION_CHECKS == ("valid", "accepted", "preserves", "scoped")


def test_e15_5_no_answer_scores_zero() -> None:
    item, flawed = one()
    result = score_correction(item.id, flawed, item.defect.slot, None, MUST_KEEP)
    assert result.score == 0.0 and result.missing == CORRECTION_CHECKS


def test_e15_5_a_repair_the_pipeline_would_refuse_loses_accepted() -> None:
    item, flawed = one()
    refused = correction(flawed_bodies(item, BASE), findings=("MANUAL_LOOP",))
    result = score_correction(item.id, flawed, item.defect.slot, refused, MUST_KEEP)
    assert result.missing == ("accepted",)


def test_e15_5_emptying_the_logic_passes_the_pre_check_but_loses_preserves() -> None:
    item, flawed = one()
    emptied = CodeBodies.model_validate(
        {**BASE["bodies"], "check_body": "return {}\n", "action_body": "return {}\n", "helpers": ""}
    )
    result = score_correction(item.id, flawed, item.defect.slot, correction(emptied), MUST_KEEP)
    assert "preserves" in result.missing and "accepted" not in result.missing


def test_e15_5_touching_another_slot_loses_scoped() -> None:
    item, flawed = one()  # the flaw is in check_body; the action body must stay as it was
    meddled = CodeBodies.model_validate(
        {**BASE["bodies"], "action_body": BASE["bodies"]["action_body"] + "extra = 1\n"}
    )
    result = score_correction(item.id, flawed, item.defect.slot, correction(meddled), MUST_KEEP)
    assert result.missing == ("scoped",)


def test_e15_5_rewriting_the_sub_skills_behaviour_loses_scoped() -> None:
    item, flawed = one()
    patched = correction(CLEAN, patch_class=IsatvonPatchClass.BEHAVIOUR)
    result = score_correction(item.id, flawed, item.defect.slot, patched, MUST_KEEP)
    assert result.missing == ("scoped",)
    neutral = correction(CLEAN, patch_class=IsatvonPatchClass.NEUTRAL)
    assert score_correction(item.id, flawed, item.defect.slot, neutral, MUST_KEEP).score == 1.0


# --- the gate -------------------------------------------------------------------------------


def runner_of(answer: Callable[[CodeBodies], Correction | None]) -> Callable[[], CorrectionRunner]:
    def make() -> CorrectionRunner:
        async def run(
            sub_skill: Mapping[str, Any], bodies: CodeBodies, findings: list[dict[str, Any]]
        ) -> Correction | None:
            assert findings, "the corrector is always given findings"
            return answer(bodies)

        return run

    return make


def sandbox(tmp_path: Path) -> dict[str, Path]:
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    for name in ("tasks.json", "correction.json", "correction-base.json"):
        (golden / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "gate.json").write_text((EVALS_DIR / "gate.json").read_text(encoding="utf-8"))
    return {"golden_dir": golden, "evals_dir": tmp_path, "baseline_dir": tmp_path / "baselines"}


def run(factory: Callable[[], CorrectionRunner], tmp_path: Path, **kwargs: Any) -> GateReport:
    return asyncio.run(run_correction_gate(factory, **sandbox(tmp_path), **kwargs))  # type: ignore[arg-type]


def test_e15_5_recording_a_correction_baseline_pins_the_corrector_prompt(tmp_path: Path) -> None:
    report = run(runner_of(lambda _: correction(CLEAN)), tmp_path, record=True, runs=1)
    written = json.loads((tmp_path / "baselines" / "correction.json").read_text(encoding="utf-8"))
    newest = PromptRegistry().get("correct-code")
    assert report.decision.verdict is Verdict.PASS and report.score == 1.0
    assert written["task"] == "correction" and written["alias"] == "sbam-corrector"
    assert written["items"] == 20
    assert written["prompt_sha256"] == {f"correct-code/{newest.version}": newest.sha256}


def test_e15_5_a_good_corrector_passes_and_a_bad_one_is_blocked(tmp_path: Path) -> None:
    run(runner_of(lambda _: correction(CLEAN)), tmp_path, record=True, runs=1)
    assert (
        run(runner_of(lambda _: correction(CLEAN)), tmp_path, runs=1).decision.verdict
        is Verdict.PASS
    )
    no_fix = run(
        runner_of(lambda bodies: correction(bodies, findings=("still",))), tmp_path, runs=1
    )
    assert no_fix.decision.verdict is Verdict.REGRESSED and EXIT[Verdict.REGRESSED] == 1
    silent = run(runner_of(lambda _: None), tmp_path, runs=1)
    assert silent.decision.verdict is Verdict.REGRESSED and silent.score == 0.0


def test_e15_5_a_provider_outage_in_correction_is_an_infra_error_never_a_pass(
    tmp_path: Path,
) -> None:
    run(runner_of(lambda _: correction(CLEAN)), tmp_path, record=True, runs=1)

    def down() -> CorrectionRunner:
        async def run_(
            sub_skill: Mapping[str, Any], bodies: CodeBodies, findings: list[dict[str, Any]]
        ) -> Correction | None:
            raise LlmTransportError("bifrost returned 503")

        return run_

    report = run(down, tmp_path, runs=1)
    assert report.decision.verdict is Verdict.INFRA_ERROR and report.score is None


def test_e15_5_each_correction_run_gets_its_own_runner() -> None:
    made = []

    def factory() -> CorrectionRunner:
        made.append(1)
        return runner_of(lambda _: correction(CLEAN))()

    results = asyncio.run(run_correction(factory, SET.items, BASE, ENV, runs=3))
    assert len(made) == 3 and [len(r) for r in results] == [20, 20, 20]
    assert all(r.score == 1.0 for run_results in results for r in run_results)


def test_e15_5_correction_is_a_ready_task() -> None:
    assert Task.CORRECTION in {t.task for t in load_tasks() if t.status == "ready"}


def test_e15_5_the_committed_correction_baseline_is_valid_and_matches_the_set() -> None:
    committed = load_baseline(Task.CORRECTION)
    assert committed is not None  # the gate has something to compare with
    assert committed.items == len(SET.items) and committed.alias == "sbam-corrector"
    assert committed.runs == load_gate_config().runs
    assert 0.0 <= committed.score <= 1.0
    assert any(key.startswith("correct-code/") for key in committed.prompt_sha256)
