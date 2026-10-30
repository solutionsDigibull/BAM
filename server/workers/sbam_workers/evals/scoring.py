"""Deterministic scoring for the golden sets (E15.5).

An item scores the share of its identifiers that came back exactly as printed. Whitespace is not
part of an identifier, because a model may break lines differently from the page, but case and
punctuation are: `ADx-2026-014` is not `ADX-2026-014`. A task scores the mean item score, averaged
over its runs so one unlucky sample does not decide the gate (the model is not deterministic).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sbam_contracts.build import IsatvonPatchClass
from sbam_contracts.testgen import TestgenOutput

from sbam_workers.build.codegen.bodies import GeneratedBodies
from sbam_workers.build.codegen.correction import Correction
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


CORRECTION_CHECKS = ("valid", "accepted", "preserves", "scoped")
_SLOTS = ("action_body", "check_body", "helpers", "state_queries")


def _text(bodies: Mapping[str, object]) -> str:
    parts: list[str] = []
    for slot in _SLOTS:
        value = bodies[slot]
        parts += list(value.values()) if isinstance(value, dict) else [str(value)]
    return "\n".join(parts)


def score_correction(
    item_id: str,
    flawed: Mapping[str, object],
    slot: str,
    correction: Correction | None,
    must_keep: Sequence[str],
) -> ItemResult:
    """Four checks, a quarter each. `valid`: the corrector gave an answer the client accepted.
    `accepted`: the pipeline would take it as it is (no pre-check or patch finding).
    `preserves`: the code that makes the Sub-Skill do its job is still there (an emptied body
    passes the pre-check). `scoped`: it left the other bodies alone and did not rewrite the
    Sub-Skill's behaviour to make the problem go away (#446); only `slot`, where the flaw is, may
    change."""
    if correction is None:
        return ItemResult(item_id, 0, len(CORRECTION_CHECKS), CORRECTION_CHECKS)
    corrected = correction.bodies.model_dump()
    untouched = all(corrected[other] == flawed[other] for other in _SLOTS if other != slot)
    passed = {
        "valid": True,
        "accepted": not correction.findings,
        "preserves": all(token in _text(corrected) for token in must_keep),
        "scoped": untouched and correction.patch_class is not IsatvonPatchClass.BEHAVIOUR,
    }
    missing = tuple(name for name in CORRECTION_CHECKS if not passed[name])
    return ItemResult(
        item_id, len(CORRECTION_CHECKS) - len(missing), len(CORRECTION_CHECKS), missing
    )


CODEGEN_CHECKS = ("valid", "accepted", "clean", "complete")


def score_codegen(
    item_id: str, generated: GeneratedBodies | None, gate_outputs: Sequence[str]
) -> ItemResult:
    """Four checks, a quarter each. `valid`: the model gave bodies the client accepted. `accepted`:
    the pre-check passed and the bodies assembled into files (the build would go on to the AST
    gate). `clean`: it took no re-ask. `complete`: the check tool names every gate output in its
    return, a structural stand-in for what the AST gate's conformance check enforces later (it does
    not prove the value is right, only that the key is there)."""
    if generated is None:
        return ItemResult(item_id, 0, len(CODEGEN_CHECKS), CODEGEN_CHECKS)
    check = generated.bodies.check_body
    passed = {
        "valid": True,
        "accepted": not generated.findings and bool(generated.files),
        "clean": generated.attempts <= 1,  # 0 is a replay-cache hit
        "complete": all(
            re.search(rf"""["']{re.escape(name)}["']""", check) for name in gate_outputs
        ),
    }
    missing = tuple(name for name in CODEGEN_CHECKS if not passed[name])
    return ItemResult(item_id, len(CODEGEN_CHECKS) - len(missing), len(CODEGEN_CHECKS), missing)
