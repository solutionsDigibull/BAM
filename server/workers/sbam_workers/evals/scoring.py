"""Deterministic scoring for the golden sets (E15.5).

An item scores the share of its identifiers that came back exactly as printed. Whitespace is not
part of an identifier, because a model may break lines differently from the page, but case and
punctuation are: `ADx-2026-014` is not `ADX-2026-014`. A task scores the mean item score, averaged
over its runs so one unlucky sample does not decide the gate (the model is not deterministic).
"""

from collections.abc import Sequence
from dataclasses import dataclass

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
