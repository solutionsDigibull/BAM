"""Golden sets for the LLM tasks (E15.5, TD §19.1): the data, its checks and the baseline format.

A golden item is what one task sends the model plus deterministic checks on the answer. The sets
are synthetic and versioned in `server/evals/golden/<task>.json`; `tasks.json` lists every task
and whether its set is `ready` (at least `min_items` valid items) or still `todo`. A set may not
claim to be ready without the items, so the gate never runs on a half-written set.

Nothing here calls a model. The runner that does is a separate change.
"""

import copy
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sbam_contracts.isatvon14 import SubSkill

EVALS_DIR: Final = Path(__file__).resolve().parents[3] / "evals"
GOLDEN_DIR: Final = EVALS_DIR / "golden"
BASELINE_DIR: Final = EVALS_DIR / "baselines"
MIN_ITEMS: Final = 20  # TD §19.1: at least 20 items per task


class Task(StrEnum):
    OCR = "ocr"
    SOW = "sow"
    ISATVON = "isatvon"
    CODEGEN = "codegen"
    TESTGEN = "testgen"
    CORRECTION = "correction"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskEntry(_Strict):
    """One row of `tasks.json`."""

    task: Task
    alias: str
    prompt_ids: list[str] = Field(min_length=1)
    status: Literal["ready", "todo"]
    min_items: int = Field(default=MIN_ITEMS, ge=MIN_ITEMS)
    note: str = ""


class RenderSpec(_Strict):
    """How an OCR item's page image is drawn. Deterministic: the same spec draws the same page, so
    the repo holds the spec and never an image."""

    lines: Annotated[list[str], Field(min_length=1)]
    font_size: int = Field(default=24, ge=10, le=80)
    rotate: float = Field(default=0.0, ge=-10.0, le=10.0)  # degrees, a crooked scan or photo
    blur: float = Field(default=0.0, ge=0.0, le=4.0)  # gaussian radius
    speckle: int = Field(default=0, ge=0, le=200)  # pixels darkened, per thousand
    shade: float = Field(default=0.0, ge=0.0, le=0.6)  # uneven light, dark towards one corner
    scale: float = Field(default=1.0, gt=0.2, le=1.0)  # 0.5 = half resolution
    jpeg_quality: int | None = Field(default=None, ge=20, le=95)  # None = lossless PNG
    seed: int = 0


class OcrItem(_Strict):
    id: Annotated[str, Field(pattern=r"^ocr-\d{2}$")]
    description: str = Field(min_length=1)
    synthetic: Literal[True]  # a golden set never holds real data
    render: RenderSpec
    # Strings that must come back exactly as printed (case and punctuation included).
    identifiers: Annotated[list[str], Field(min_length=3)]


class OcrSet(_Strict):
    task: Literal[Task.OCR]
    version: int = Field(ge=1)
    items: list[OcrItem]


class GoldenTestgenOverrides(_Strict):
    """What an item changes in the base Sub-Skill (`testgen-base.json`, the MS01.1 sample). The rest
    of the document stays as it is."""

    max_iterations: int = Field(ge=1, le=5)  # 1 removes PASS_AFTER_RETRY from the allowed kinds
    required_inputs: Annotated[list[str], Field(min_length=2)]
    fresh_state_parameters: Annotated[list[str], Field(min_length=1)]
    tables: Annotated[list[str], Field(min_length=1)]
    success_gate: str = Field(min_length=1)
    no_op_condition: str = Field(min_length=1)
    escalation_condition: str = Field(min_length=1)


class GoldenTestgenItem(_Strict):
    id: Annotated[str, Field(pattern=r"^testgen-\d{2}$")]
    description: str = Field(min_length=1)
    synthetic: Literal[True]
    overrides: GoldenTestgenOverrides


class GoldenTestgenSet(_Strict):
    task: Literal[Task.TESTGEN]
    version: int = Field(ge=1)
    items: list[GoldenTestgenItem]


def load_tasks(golden_dir: Path = GOLDEN_DIR) -> list[TaskEntry]:
    raw = json.loads((golden_dir / "tasks.json").read_text(encoding="utf-8"))
    return [TaskEntry.model_validate(row) for row in raw]


def load_ocr(golden_dir: Path = GOLDEN_DIR) -> OcrSet:
    return OcrSet.model_validate_json((golden_dir / "ocr.json").read_text(encoding="utf-8"))


def load_testgen(golden_dir: Path = GOLDEN_DIR) -> GoldenTestgenSet:
    text = (golden_dir / "testgen.json").read_text(encoding="utf-8")
    return GoldenTestgenSet.model_validate_json(text)


def testgen_base(golden_dir: Path = GOLDEN_DIR) -> dict[str, Any]:
    base: dict[str, Any] = json.loads(
        (golden_dir / "testgen-base.json").read_text(encoding="utf-8")
    )
    return base


def build_testgen_isatvon(item: GoldenTestgenItem, base: dict[str, Any]) -> dict[str, Any]:
    """The item's Sub-Skill: the base with the item's overrides applied, and validated."""
    document = copy.deepcopy(base)
    definition = document["isatvon_definition"]
    o = item.overrides
    definition["s_source"]["required_inputs"] = list(o.required_inputs)
    definition["s_source"]["fresh_state_parameters"] = list(o.fresh_state_parameters)
    variables = definition["v_variables"]
    variables["max_iterations"] = o.max_iterations
    variables["tables"] = list(o.tables)
    variables["success_gate"] = o.success_gate
    variables["no_op_condition"] = o.no_op_condition
    variables["escalation_condition"] = o.escalation_condition
    SubSkill.model_validate(document)  # a variant that is not a valid Sub-Skill cannot be a test
    return document


def check_golden(
    golden_dir: Path = GOLDEN_DIR, prompt_ids: frozenset[str] = frozenset()
) -> list[str]:
    """Every problem with the golden sets, empty when they are sound. `prompt_ids` are the ids the
    prompt registry knows; a task naming another id is a problem."""
    try:
        tasks = load_tasks(golden_dir)
    except (OSError, ValueError) as exc:
        return [f"tasks.json: {type(exc).__name__}"]
    problems = [
        f"tasks.json: {t} is listed more than once" for t in _repeats([e.task for e in tasks])
    ]
    problems += [f"tasks.json: {t} is missing" for t in Task if t not in {e.task for e in tasks}]
    for entry in tasks:
        for prompt_id in entry.prompt_ids:
            if prompt_ids and prompt_id not in prompt_ids:
                problems.append(f"{entry.task}: unknown prompt {prompt_id!r}")
        if entry.status == "ready":
            problems += _check_ready(entry, golden_dir)
        elif (golden_dir / f"{entry.task}.json").exists():
            problems.append(f"{entry.task}: has a set but is listed as todo")
    return problems


def _check_ready(entry: TaskEntry, golden_dir: Path) -> list[str]:
    if entry.task is Task.OCR:
        return _check_ocr(entry, golden_dir)
    if entry.task is Task.TESTGEN:
        return _check_testgen(entry, golden_dir)
    return [f"{entry.task}: listed as ready, but no loader for its items exists yet"]


def _check_count_and_ids(entry: TaskEntry, ids: list[str]) -> list[str]:
    problems = []
    if len(ids) < entry.min_items:
        problems.append(f"{entry.task}: {len(ids)} items, at least {entry.min_items} needed")
    problems += [f"{entry.task}: id {i} is used more than once" for i in _repeats(ids)]
    return problems


def _check_ocr(entry: TaskEntry, golden_dir: Path) -> list[str]:
    try:
        items = load_ocr(golden_dir).items
    except (OSError, ValidationError, ValueError) as exc:
        return [f"{entry.task}: {type(exc).__name__}"]
    problems = _check_count_and_ids(entry, [x.id for x in items])
    for item in items:
        printed = "\n".join(item.render.lines)
        problems += [
            f"{item.id}: identifier {ident!r} is not printed on the page"
            for ident in item.identifiers
            if ident not in printed
        ]
    return problems


def _check_testgen(entry: TaskEntry, golden_dir: Path) -> list[str]:
    try:
        items = load_testgen(golden_dir).items
        base = testgen_base(golden_dir)
    except (OSError, ValidationError, ValueError) as exc:
        return [f"{entry.task}: {type(exc).__name__}"]
    problems = _check_count_and_ids(entry, [x.id for x in items])
    for item in items:
        try:
            build_testgen_isatvon(item, base)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"{item.id}: not a valid Sub-Skill ({type(exc).__name__})")
    return problems


def _repeats[T](values: list[T]) -> list[T]:
    seen: set[T] = set()
    again: set[T] = set()
    for value in values:
        (again if value in seen else seen).add(value)
    return sorted(again, key=str)
