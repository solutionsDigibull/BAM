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


class DomainProfile(_Strict):
    """One invented business domain: every field of a Sub-Skill's ISATVON that names it.

    A variant is the base Sub-Skill (`testgen-base.json`, only its structure is kept) written out
    for a domain, so its prose, tables, outputs and handoff all agree. A variant that only swapped
    the tables and inputs still told the model about the base's login tables, and it wrote into
    them."""

    macro_category: str
    skill_id: str
    skill_name: str
    trigger: str
    action: str
    check: str
    actor_level: str
    actor_persona: str
    actor_authority: str
    goal: str
    method: str
    background: str
    topic: str
    history: str
    required_inputs: Annotated[list[str], Field(min_length=2)]
    fresh_state_parameters: Annotated[list[str], Field(min_length=1)]
    action_step: str
    acceptance_check: str
    action_tool: str
    fixed_check_tool: str
    libraries: Annotated[list[str], Field(min_length=1)]  # package names
    success_gate: str
    no_op_condition: str
    escalation_condition: str
    tables: Annotated[list[str], Field(min_length=2, max_length=2)]  # [state table, handoff table]
    parameter_name: str
    parameter_value: int
    secrets: Annotated[list[str], Field(min_length=1)]
    outputs: dict[str, Any]  # o_outcome.json_schema
    handoff_fields: Annotated[list[str], Field(min_length=1)]  # after `iteration_pass`
    handoff_description: str
    success_criteria: str
    loop_update: str  # shown from the second pass; `{n}` is the bound
    escalated_update: str
    db_update: str
    alert: str
    next_skill: str


class GoldenTestgenItem(_Strict):
    id: Annotated[str, Field(pattern=r"^testgen-\d{2}$")]
    description: str = Field(min_length=1)
    synthetic: Literal[True]
    domain: str = Field(min_length=1)
    max_iterations: int = Field(ge=1, le=5)  # 1 removes PASS_AFTER_RETRY from the allowed kinds


class GoldenTestgenSet(_Strict):
    task: Literal[Task.TESTGEN]
    version: int = Field(ge=1)
    domains: dict[str, DomainProfile]
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


_LIBRARY_VERSIONS: Final = {"fastmcp": ">=2.0", "psycopg": ">=3.2"}


def build_testgen_isatvon(
    item: GoldenTestgenItem, base: dict[str, Any], domains: dict[str, DomainProfile]
) -> dict[str, Any]:
    """The item's Sub-Skill: the base's structure written out for the item's domain and bound, and
    validated. Nothing of the base's own domain is kept."""
    p, n = domains[item.domain], item.max_iterations
    retries = (
        f"Allow up to {n} retry loops before escalating"
        if n > 1
        else ("No retries: escalate when the first check fails")
    )
    doc = copy.deepcopy(base)
    doc["skill_metadata"] = {
        "macro_category": p.macro_category,
        "skill_id": p.skill_id,
        "skill_name": p.skill_name,
    }
    d = doc["isatvon_definition"]
    d["i_instructions"] = {
        "statement": (
            f"Trigger: {p.trigger}. Action: {p.action}. Check: {p.check}. Bound: {retries}."
        ),
        "trigger": p.trigger,
        "action": p.action,
        "check": p.check,
        "bound": retries,
        "user_role": {
            "level": p.actor_level,
            "persona": p.actor_persona,
            "authority": p.actor_authority,
        },
        "task": {
            "goal": p.goal,
            "method": p.method,
            "steps": [
                f"Trigger: {p.trigger}",
                f"Action Step: {p.action}",
                f"Fixed Check: {p.check}",
                f"Loop or Resolve: retry up to {n} times, then resolve or escalate",
            ],
        },
        "context": {"background": p.background, "topic": p.topic, "history": p.history},
    }
    d["s_source"] = {
        "required_inputs": list(p.required_inputs),
        "fresh_state_parameters": list(p.fresh_state_parameters),
    }
    d["a_automation"].update(
        action_step=p.action_step,
        acceptance_check=p.acceptance_check,
        srim_metadata=f"FastMCP_Execution_Engine with PostgreSQL JSONB state in {p.tables[0]}",
    )
    d["t_tech_stack"].update(
        action_tool=p.action_tool,
        fixed_check_tool=p.fixed_check_tool,
        libraries=[
            {
                "package": lib,
                **({"version_constraint": v} if (v := _LIBRARY_VERSIONS.get(lib)) else {}),
            }
            for lib in p.libraries
        ],
    )
    d["v_variables"].update(
        max_iterations=n,
        success_gate=p.success_gate,
        no_op_condition=p.no_op_condition,
        escalation_condition=p.escalation_condition,
        tables=list(p.tables),
        parameters=[{"name": p.parameter_name, "type": "integer", "value": p.parameter_value}],
        secrets=list(p.secrets),
    )
    d["o_outcome"] = {
        "json_schema": copy.deepcopy(p.outputs),
        "state_handoff_file": {
            "table": p.tables[1],
            "mode": "append_only",
            "required_fields": ["iteration_pass", *p.handoff_fields],
            "description": p.handoff_description,
        },
        "success_criteria": p.success_criteria,
    }
    d["n_notification"] = {
        "ui_updates": [
            p.loop_update.format(n=n) if n > 1 else "Status: Verifying -> Resolved",
            p.escalated_update,
        ],
        "database_updates": [p.db_update],
        "alerts": [p.alert],
        "next": p.next_skill,
    }
    SubSkill.model_validate(doc)  # a variant that is not a valid Sub-Skill cannot be a test
    return doc


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
        loaded = load_testgen(golden_dir)
        base = testgen_base(golden_dir)
    except (OSError, ValidationError, ValueError) as exc:
        return [f"{entry.task}: {type(exc).__name__}"]
    items = loaded.items
    problems = _check_count_and_ids(entry, [x.id for x in items])
    for item in items:
        try:
            build_testgen_isatvon(item, base, loaded.domains)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"{item.id}: not a valid Sub-Skill ({type(exc).__name__})")
    return problems


def _repeats[T](values: list[T]) -> list[T]:
    seen: set[T] = set()
    again: set[T] = set()
    for value in values:
        (again if value in seen else seen).add(value)
    return sorted(again, key=str)
