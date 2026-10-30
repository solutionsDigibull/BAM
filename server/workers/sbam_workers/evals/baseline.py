"""Baselines and the gate's verdict (E15.5).

A baseline is the last accepted score of a task, stored in `server/evals/baselines/<task>.json`
with the prompt versions and model it was measured on. The gate compares a fresh score with it:

- `PASS`: the score did not drop by more than the threshold (an improvement passes).
- `REGRESSED`: it dropped by more than the threshold. The PR is blocked.
- `NO_BASELINE`: a new task. The gate does not pass it silently: a person reviews the first score
  and commits it (`--record`).
- `INFRA_ERROR`: the provider failed, so there is no score. That is a failure, never a pass.

The threshold and the run count live in `server/evals/gate.json`; changing them is a reviewed
change like any other file in the repo.
"""

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sbam_workers.evals.golden import BASELINE_DIR, EVALS_DIR, Task


class Verdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - a verdict, not a password
    REGRESSED = "REGRESSED"
    NO_BASELINE = "NO_BASELINE"
    INFRA_ERROR = "INFRA_ERROR"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateConfig(_Strict):
    threshold: float = Field(gt=0.0, le=0.5)  # the largest drop that still passes
    runs: int = Field(ge=1, le=10)  # runs averaged per item (the model is not deterministic)


class Baseline(_Strict):
    task: Task
    score: float = Field(ge=0.0, le=1.0)
    items: int = Field(ge=1)
    runs: int = Field(ge=1)
    alias: str = Field(min_length=1)  # the `sbam-*` alias; Bifrost binds the real model to it
    prompt_sha256: dict[str, str] = Field(min_length=1)  # prompt id/version -> sha256
    recorded_on: date
    note: str = ""


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str


def load_gate_config(evals_dir: Path = EVALS_DIR) -> GateConfig:
    return GateConfig.model_validate_json((evals_dir / "gate.json").read_text(encoding="utf-8"))


def baseline_path(task: Task, baseline_dir: Path = BASELINE_DIR) -> Path:
    return baseline_dir / f"{task}.json"


def load_baseline(task: Task, baseline_dir: Path = BASELINE_DIR) -> Baseline | None:
    path = baseline_path(task, baseline_dir)
    if not path.exists():
        return None
    return Baseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_baseline(baseline: Baseline, baseline_dir: Path = BASELINE_DIR) -> Path:
    path = baseline_path(baseline.task, baseline_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return path


def decide(baseline: Baseline | None, score: float, threshold: float) -> Decision:
    if baseline is None:
        return Decision(
            Verdict.NO_BASELINE,
            f"no accepted baseline; score {score:.3f}. "
            "Record it with --record and have it reviewed",
        )
    drop = round(baseline.score - score, 9)  # 0.9 - 0.85 is 0.05000000000000004: a drop of exactly
    if drop > threshold:  # the threshold must pass
        return Decision(
            Verdict.REGRESSED,
            f"score {score:.3f} is {drop:.3f} below the baseline {baseline.score:.3f} "
            f"(the threshold is {threshold:.3f})",
        )
    return Decision(Verdict.PASS, f"score {score:.3f}, baseline {baseline.score:.3f}")
