"""Deterministic scoring for the golden sets (E15.5).

An item scores the share of its identifiers that came back exactly as printed. Whitespace is not
part of an identifier, because a model may break lines differently from the page, but case and
punctuation are: `ADx-2026-014` is not `ADX-2026-014`. A task scores the mean item score, averaged
over its runs so one unlucky sample does not decide the gate (the model is not deterministic).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sbam_contracts.testgen import TestgenOutput

from sbam_workers.evals.golden import OcrItem


@dataclass(frozen=True)
class ItemResult:
    item_id: str
    found: int
    total: int
    missing: tuple[str, ...]

    @property
    def score(self) -> float:
        return self.found / self.total if self.total else 0.0


def _squeeze(text: str) -> str:
    return "".join(text.split())


def score_ocr(item: OcrItem, text: str) -> ItemResult:
    """`text` is what the model read from the page (empty when the page was unreadable)."""
    flat = _squeeze(text)
    missing = tuple(i for i in item.identifiers if _squeeze(i) not in flat)
    return ItemResult(item.id, len(item.identifiers) - len(missing), len(item.identifiers), missing)


def run_score(results: Sequence[ItemResult]) -> float:
    if not results:
        raise ValueError("a run with no items has no score")
    return sum(r.score for r in results) / len(results)


def task_score(runs: Sequence[Sequence[ItemResult]]) -> float:
    """The mean of the runs' scores."""
    if not runs:
        raise ValueError("no runs to score")
    return sum(run_score(r) for r in runs) / len(runs)


TESTGEN_CHECKS = ("valid", "clean", "covers_inputs")


def score_testgen(
    item_id: str,
    required_inputs: Sequence[str],
    output: TestgenOutput | None,
    attempts: int,
) -> ItemResult:
    """Three checks, each worth a third. `valid`: the model produced cases the API would accept
    (the production re-ask loop validates every case; `None` means it never did). `clean`: it did so
    with no re-ask (`attempts` is the number of model calls). `covers_inputs`: every input the
    Sub-Skill requires appears in the input of at least one case, so the tests exercise it."""
    covers = output is not None and all(
        any(name in case.input for case in output.cases) for name in required_inputs
    )
    passed = {
        "valid": output is not None,
        "clean": output is not None and attempts == 1,
        "covers_inputs": covers,
    }
    missing = tuple(name for name in TESTGEN_CHECKS if not passed[name])
    return ItemResult(item_id, len(TESTGEN_CHECKS) - len(missing), len(TESTGEN_CHECKS), missing)
