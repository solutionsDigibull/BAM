"""E15.5: scoring, baselines and the gate runner, with fake readers (no model is called)."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sbam_workers.evals import gate
from sbam_workers.evals.baseline import (
    Baseline,
    Verdict,
    decide,
    load_baseline,
    load_gate_config,
)
from sbam_workers.evals.gate import EXIT, GateReport, InfraError, OcrReader, run_gate, run_ocr
from sbam_workers.evals.golden import EVALS_DIR, GOLDEN_DIR, Task, load_ocr
from sbam_workers.evals.scoring import ItemResult, run_score, score_ocr, task_score
from sbam_workers.llm.client import LlmTransportError

ITEMS = load_ocr().items
EVERYTHING = " ".join(sorted({i for item in ITEMS for i in item.identifiers}))
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def reader_of(text: str) -> Callable[[], OcrReader]:
    async def read(data: bytes, media_type: str) -> str:
        return text

    return lambda: read


def sandbox(tmp_path: Path) -> dict[str, Path]:
    """A copy of the golden dir and gate config, with an empty baseline dir."""
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    for name in (
        "tasks.json",
        "ocr.json",
        "testgen.json",
        "testgen-base.json",
        "correction.json",
        "correction-base.json",
        "codegen.json",
    ):
        (golden / name).write_text(
            (GOLDEN_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "gate.json").write_text((EVALS_DIR / "gate.json").read_text(encoding="utf-8"))
    return {"golden_dir": golden, "evals_dir": tmp_path, "baseline_dir": tmp_path / "baselines"}


def run(make_reader: Callable[[], OcrReader], tmp_path: Path, **kwargs: object) -> GateReport:
    return asyncio.run(run_gate(make_reader, today=lambda: NOW, **sandbox(tmp_path), **kwargs))  # type: ignore[arg-type]


# --- scoring --------------------------------------------------------------------------------


def test_e15_5_an_item_scores_the_share_of_identifiers_that_came_back_exactly() -> None:
    item = ITEMS[0]
    assert score_ocr(item, EVERYTHING).score == 1.0
    partial = score_ocr(item, EVERYTHING.replace(item.identifiers[0], ""))
    assert partial.missing == (item.identifiers[0],)
    assert partial.found == len(item.identifiers) - 1
    assert score_ocr(item, "").score == 0.0


def test_e15_5_line_breaks_do_not_matter_but_a_wrong_character_does() -> None:
    item = ITEMS[0]
    spread = "\n".join(ident for ident in item.identifiers)
    assert score_ocr(item, spread).score == 1.0
    wrong = EVERYTHING.replace("RFQ-7741-ZA", "RFQ-7741-ZA".replace("Z", "z"))
    assert "RFQ-7741-ZA" in score_ocr(item, wrong).missing  # case is part of the identifier


def test_e15_5_a_task_scores_the_mean_over_its_runs() -> None:
    def results(found: int) -> list[ItemResult]:
        return [ItemResult("a", found, 2, ()), ItemResult("b", found, 2, ())]

    assert run_score(results(1)) == 0.5
    assert task_score([results(2), results(0), results(2)]) == pytest.approx(2 / 3)
    with pytest.raises(ValueError):
        task_score([])
    with pytest.raises(ValueError):
        run_score([])


# --- the verdict ----------------------------------------------------------------------------


def baseline(score: float = 0.9) -> Baseline:
    return Baseline(
        task=Task.OCR,
        score=score,
        items=20,
        runs=3,
        alias="sbam-ocr",
        prompt_sha256={"ocr-page/1.0.0": "a" * 64},
        recorded_on=NOW.date(),
    )


@pytest.mark.parametrize(
    ("score", "verdict"),
    [
        (0.95, Verdict.PASS),  # an improvement passes
        (0.9, Verdict.PASS),
        (0.85, Verdict.PASS),  # a drop of exactly the threshold passes
        (0.84, Verdict.REGRESSED),  # more than the threshold blocks
        (0.0, Verdict.REGRESSED),
    ],
)
def test_e15_5_t02_a_drop_larger_than_the_threshold_regresses(
    score: float, verdict: Verdict
) -> None:
    assert decide(baseline(0.9), score, 0.05).verdict is verdict


def test_e15_5_a_task_with_no_baseline_is_not_passed_silently() -> None:
    decision = decide(None, 0.97, 0.05)
    assert decision.verdict is Verdict.NO_BASELINE and "--record" in decision.reason


def test_e15_5_the_gate_config_and_baseline_formats_are_strict(tmp_path: Path) -> None:
    config = load_gate_config()
    assert 0 < config.threshold <= 0.5 and config.runs >= 1
    with pytest.raises(ValidationError):
        Baseline.model_validate({**baseline().model_dump(mode="json"), "extra": 1})
    with pytest.raises(ValidationError):
        Baseline.model_validate({**baseline().model_dump(mode="json"), "score": 1.5})
    assert load_baseline(Task.OCR, tmp_path) is None


# --- the runner -----------------------------------------------------------------------------


def test_e15_5_a_perfect_reader_scores_one_and_each_run_gets_its_own_reader() -> None:
    made = []

    def factory() -> OcrReader:
        made.append(1)
        return reader_of(EVERYTHING)()

    results = asyncio.run(run_ocr(factory, ITEMS, runs=3))
    assert len(made) == 3  # a shared reader would cache run 1's answers for runs 2 and 3
    assert [len(r) for r in results] == [20, 20, 20]
    assert task_score(results) == 1.0


def test_e15_5_recording_writes_a_baseline_with_the_prompt_versions(tmp_path: Path) -> None:
    report = run(reader_of(EVERYTHING), tmp_path, record=True)
    written = json.loads((tmp_path / "baselines" / "ocr.json").read_text(encoding="utf-8"))
    assert report.decision.verdict is Verdict.PASS and report.score == 1.0
    assert written["score"] == 1.0 and written["items"] == 20 and written["runs"] == 3
    assert written["alias"] == "sbam-ocr"
    assert list(written["prompt_sha256"]) == ["ocr-page/1.0.0"]
    assert len(written["prompt_sha256"]["ocr-page/1.0.0"]) == 64
    assert written["recorded_on"] == "2026-10-30"


def test_e15_5_the_committed_ocr_baseline_is_valid_and_matches_the_set() -> None:
    committed = load_baseline(Task.OCR)
    assert committed is not None  # the gate has something to compare with
    assert committed.items == len(ITEMS) and committed.alias == "sbam-ocr"
    assert committed.runs == load_gate_config().runs
    assert 0.0 <= committed.score <= 1.0
    assert "ocr-page/1.0.0" in committed.prompt_sha256


def test_e15_5_a_baseline_that_cannot_be_written_still_reports_the_score(tmp_path: Path) -> None:
    blocker = tmp_path / "baselines"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    report = run(reader_of(EVERYTHING), tmp_path, record=True)
    assert report.decision.verdict is Verdict.INFRA_ERROR  # not recorded, so not a pass
    assert report.score == 1.0 and "scored 1.0000" in report.decision.reason


def test_e15_5_t01_a_run_that_matches_the_baseline_passes(tmp_path: Path) -> None:
    run(reader_of(EVERYTHING), tmp_path, record=True)
    report = run(reader_of(EVERYTHING), tmp_path)
    assert report.decision.verdict is Verdict.PASS and EXIT[Verdict.PASS] == 0


def test_e15_5_t02_a_worse_prompt_blocks_the_change(tmp_path: Path) -> None:
    run(reader_of(EVERYTHING), tmp_path, record=True)
    worse = EVERYTHING
    for lost in ("RFQ-7741-ZA", "HX-220-B", "VND-30458", "PO-2026-88213", "C-5521"):
        worse = worse.replace(lost, "")  # five ids lost, across two of the five documents
    report = run(reader_of(worse), tmp_path)
    assert report.decision.verdict is Verdict.REGRESSED and EXIT[Verdict.REGRESSED] == 1
    assert "below the baseline" in report.decision.reason
    assert any("HX-220-B" in line for line in gate.render_report(report).splitlines())


def test_e15_5_a_small_wobble_inside_the_threshold_does_not_block(tmp_path: Path) -> None:
    run(reader_of(EVERYTHING), tmp_path, record=True)
    wobble = EVERYTHING.replace("2026-12-01", "").replace("HX-220-B", "")  # two ids lost: -0.033
    report = run(reader_of(wobble), tmp_path)
    assert report.score is not None and 0.95 < report.score < 1.0
    assert report.decision.verdict is Verdict.PASS


def test_e15_5_a_new_task_without_a_baseline_waits_for_review(tmp_path: Path) -> None:
    report = run(reader_of(EVERYTHING), tmp_path)
    assert report.decision.verdict is Verdict.NO_BASELINE and EXIT[Verdict.NO_BASELINE] == 3
    assert not (tmp_path / "baselines").exists()  # nothing is recorded behind anyone's back


def test_e15_5_t05_a_provider_outage_is_an_infra_error_never_a_pass(tmp_path: Path) -> None:
    run(reader_of(EVERYTHING), tmp_path, record=True)

    async def down(data: bytes, media_type: str) -> str:
        raise LlmTransportError("bifrost returned 503")

    report = run(lambda: down, tmp_path)
    assert report.decision.verdict is Verdict.INFRA_ERROR and report.score is None
    assert EXIT[Verdict.INFRA_ERROR] == 2 and EXIT[Verdict.INFRA_ERROR] != EXIT[Verdict.PASS]


def test_e15_5_nondeterminism_is_averaged_over_the_runs(tmp_path: Path) -> None:
    run(reader_of(EVERYTHING), tmp_path, record=True)
    calls = {"n": 0}

    def alternating() -> OcrReader:
        calls["n"] += 1
        return reader_of("" if calls["n"] == 2 else EVERYTHING)()  # run 2 reads nothing

    report = run(alternating, tmp_path)
    assert report.score == pytest.approx(
        2 / 3
    )  # one bad run of three, averaged, not decisive alone
    assert (
        report.decision.verdict is Verdict.REGRESSED
    )  # but 0.333 below the baseline is still a drop


def test_e15_5_an_infra_error_carries_no_partial_score() -> None:
    async def down(data: bytes, media_type: str) -> str:
        raise LlmTransportError("timeout")

    with pytest.raises(InfraError):
        asyncio.run(run_ocr(lambda: down, ITEMS, runs=1))
