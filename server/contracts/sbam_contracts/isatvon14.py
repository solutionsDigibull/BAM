"""ISATVON 1.4 contract: Agents, Sub-Skills and the TD §10.4 rules (TD §10.1-10.5, issue E0.2).

A Workflow is broken into **Agents** (the TD's "Macro Skill", id `MSnn`, `macro_category`
"MSnn_<Name>") and each Agent into **Sub-Skills** (`skill_id` "MSnn.m_<Name>"). Every Sub-Skill
carries a strict, structured ISATVON 1.4 definition (I/S/A/T/V/O/N). This is SBAM's build form;
the flat ISA14 export shape of the Skill Manifest is modelled in `sbam_contracts.manifest` (#20).

Rules worth knowing
  * Two layers. `SubSkill.model_validate` / `Agent.model_validate` check structure only (strict
    types, no coercion, required fields non-empty, control characters stripped, lengths capped).
    The rules of TD §10.4 run in `validate_sub_skill` / `validate_agent`, which return
    `RuleViolation`s with the rule number and a dotted path. An empty list means VALID. Structure
    errors are rule 1.
  * N is Next + Notification. `n_notification.next` names the Sub-Skill that runs next in the same
    Agent; the last Sub-Skill names the next Agent's `macro_category`; the last Sub-Skill of the
    last Agent has `next: null`. The chain is linear: every Sub-Skill is visited exactly once.
  * Gate expressions use the safe grammar in `sbam_contracts.gate_expr`, never Python eval.
  * Text is data: it is never rendered or executed as code.
  * Regenerate the committed JSON Schemas from `server/` with `python -m sbam_contracts.isatvon14`.
"""

import json
import re
import sys
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, ValidationError

from sbam_contracts import gate_expr

FRAMEWORK_VERSION: Final = "1.4"
SHORT_CHARS: Final = 200
TEXT_CHARS: Final = 4000
MAX_ITERATIONS_RANGE: Final = (1, 10)
SCHEMA_DIR: Final = Path(__file__).resolve().parent.parent / "schemas" / "isatvon"

SKILL_ID = re.compile(r"^MS(\d{2})\.(\d+)_[A-Za-z0-9_]+$")
MACRO_CATEGORY = re.compile(r"^MS(\d{2})_[A-Za-z0-9_]+$")
AGENT_REF = re.compile(r"^AG-[A-Z0-9][A-Z0-9-]*$")
SKILL_REF = re.compile(r"^SK-[A-Z0-9][A-Z0-9-]*$")
RULE_REF = re.compile(r"^RULE-[A-Z0-9][A-Z0-9-]*$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BUDGET = re.compile(r"^\s*(\d{1,9})\s*(ms|s)(?:\s+(.*))?$", re.DOTALL)
LOOP_PROGRESS = re.compile(r"Looping \[Pass [^/\]]+/[^\]]+\]")
END_MESSAGE = re.compile(
    r"\b(escalat\w*|complet\w*|done|finished|success\w*|resolved|authenticated)\b", re.IGNORECASE
)
SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
SECRET_VALUE = re.compile(r"sk-|-----BEGIN|AKIA[0-9A-Z]{8}|Bearer\s|^[A-Za-z0-9+/=_-]{40,}$")
FORBIDDEN_LIBRARIES: Final = frozenset({"duckdb"})  # PostgreSQL only
# Stand-in for the SBAM runtime registry until the skill-runtime image publishes one; injectable.
DEFAULT_LIBRARY_REGISTRY: Final = frozenset(
    {
        "fastmcp",
        "mcp",
        "pydantic",
        "httpx",
        "psycopg",
        "psycopg-pool",
        "asyncpg",
        "sqlalchemy",
        "jinja2",
        "orjson",
        "pyjwt",
        "python-dateutil",
        "tenacity",
    }
)
_OUTPUT_TYPES: Final = frozenset({"string", "integer", "number", "boolean", "object", "array"})


def _clean(value: str) -> str:
    """Strip control characters (tab and newline kept) and require something left."""
    cleaned = "".join(
        ch for ch in value if ch in "\t\n" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    if not cleaned.strip():
        raise ValueError("must not be empty")
    return cleaned


ShortText = Annotated[
    str, Field(strict=True, min_length=1, max_length=SHORT_CHARS), AfterValidator(_clean)
]
Text = Annotated[
    str, Field(strict=True, min_length=1, max_length=TEXT_CHARS), AfterValidator(_clean)
]
ExprText = Annotated[
    str, Field(strict=True, min_length=1, max_length=gate_expr.MAX_LENGTH), AfterValidator(_clean)
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- I: instructions ------------------------------------------------------------------------------


class UserRole(_Strict):
    level: ShortText
    persona: Text
    authority: Text


class Task(_Strict):
    goal: Text
    method: Text
    steps: list[Text] = Field(min_length=1)


class Context(_Strict):
    background: Text
    topic: Text
    history: Text | None = None


class IInstructions(_Strict):
    statement: (
        Text  # "Triggered by … Action: … Check: … Bound: …" (exported as ISA14 i_instructions)
    )
    trigger: Text
    action: Text
    check: Text
    bound: Text
    user_role: UserRole
    task: Task
    context: Context


# --- S: source ------------------------------------------------------------------------------------


class ExternalKind(StrEnum):
    FILE = "FILE"
    API = "API"
    URL = "URL"


class ExternalData(_Strict):
    name: ShortText
    kind: ExternalKind
    ref: Text  # pointer only, never fetched by the contract


class SSource(_Strict):
    required_inputs: list[ShortText] = Field(min_length=1)
    fresh_state_parameters: list[ShortText]
    external_data: list[ExternalData] | None = None
    ml_data: Text | None = None


# --- A: automation --------------------------------------------------------------------------------


class AAutomation(_Strict):
    orchestration_layer: ShortText
    trigger_mode: ShortText
    action_step: Text
    acceptance_check: Text
    srim_metadata: Text


# --- T: tech stack --------------------------------------------------------------------------------


class Library(_Strict):
    package: ShortText
    version_constraint: ShortText | None = None


class TTechStack(_Strict):
    execution_layer: ShortText
    action_tool: ShortText
    fixed_check_tool: ShortText
    secondary_tools: list[ShortText] | None = None
    gateway_routing: ShortText | None = None
    libraries: list[Library] = Field(min_length=1)


# --- V: variables ---------------------------------------------------------------------------------


class LoopBounds(_Strict):
    """The five mandatory ISATVON 1.4 loop bounds."""

    max_iterations: int
    success_gate: ExprText
    no_op_condition: ExprText
    escalation_condition: ExprText
    budget_limit: ShortText  # "<n>ms" or "<n>s", optional per-cycle text


class Parameter(_Strict):
    name: ShortText
    type: ShortText
    value: JsonValue = None
    default: JsonValue = None
    constraints: Text | None = None


class VVariables(LoopBounds):
    tables: list[ShortText]
    parameters: list[Parameter] | None = None
    secrets: list[ShortText] | None = None  # names only, never values

    def loop_bounds(self) -> LoopBounds:
        return LoopBounds.model_validate(self.model_dump(include=set(LoopBounds.model_fields)))


# --- O: outcome -----------------------------------------------------------------------------------


class StateHandoffFile(_Strict):
    table: ShortText
    mode: ShortText  # must be "append_only" (rule 7)
    required_fields: list[ShortText]
    description: Text


class OOutcome(_Strict):
    json_schema: dict[str, JsonValue] = Field(min_length=1)
    state_handoff_file: StateHandoffFile
    success_criteria: Text


# --- N: next + notification -----------------------------------------------------------------------


class NNotification(_Strict):
    ui_updates: list[Text]
    database_updates: list[Text]
    alerts: list[Text]
    next: ShortText | None  # next Sub-Skill, else the next Agent, else null (end of Workflow)


class IsatvonDefinition(_Strict):
    i_instructions: IInstructions
    s_source: SSource
    a_automation: AAutomation
    t_tech_stack: TTechStack
    v_variables: VVariables
    o_outcome: OOutcome
    n_notification: NNotification


class SkillMetadata(_Strict):
    macro_category: ShortText
    skill_id: ShortText
    skill_name: ShortText
    skill_ref: ShortText | None = None  # Skills master, SK-*
    rule_refs: list[ShortText] = Field(default_factory=list)  # Rules master, RULE-*


class SubSkill(_Strict):
    skill_metadata: SkillMetadata
    isatvon_definition: IsatvonDefinition


class Agent(_Strict):
    """An Agent (TD: Macro Skill): a major module of the solution, made of ordered Sub-Skills."""

    macro_category: ShortText
    name: ShortText
    description: Text
    order: int
    sow_refs: list[ShortText]
    agent_ref: ShortText | None = None  # Agents master, AG-*
    sub_skills: list[SubSkill] = Field(min_length=1)


class RuleViolation(_Strict):
    rule: int = Field(ge=1, le=10)
    path: str
    message: str


# --- Helpers --------------------------------------------------------------------------------------


def parse_budget_ms(text: str) -> int:
    """'500ms' -> 500, '2s' -> 2000, '5000ms timeout per validation cycle' -> 5000."""
    match = BUDGET.match(text)
    if match is None:
        raise ValueError(f"budget_limit {text!r} is not '<n>ms' or '<n>s'")
    amount = int(match.group(1)) * (1000 if match.group(2) == "s" else 1)
    if amount <= 0:
        raise ValueError("budget_limit must be more than 0 ms")
    return amount


def output_fields(json_schema: Mapping[str, Any]) -> list[str]:
    """Output field names of `o_outcome.json_schema` (real JSON Schema or the ISA14 short form)."""
    properties = json_schema.get("properties")
    if json_schema.get("type") == "object" and isinstance(properties, Mapping):
        return list(properties)
    return list(json_schema)


def _output_property(spec: Any) -> dict[str, Any]:
    if isinstance(spec, str) and spec in _OUTPUT_TYPES:
        return {"type": spec}
    if isinstance(spec, list) and spec and all(isinstance(v, str) for v in spec):
        return {"type": "string", "enum": list(spec)}
    if isinstance(spec, Mapping) and isinstance(spec.get("type"), str):
        return dict(spec)
    raise ValueError(f"cannot convert {spec!r} to a JSON Schema property")


def build_contracts(sub_skill: SubSkill) -> dict[str, dict[str, Any]]:
    """`contracts.input_schema` / `output_schema` for the Skill Manifest (rule 6)."""
    definition = sub_skill.isatvon_definition
    typed = {p.name: p.type for p in definition.v_variables.parameters or []}
    inputs = definition.s_source.required_inputs
    schema = definition.o_outcome.json_schema
    properties = schema.get("properties") if schema.get("type") == "object" else schema
    if not isinstance(properties, Mapping):
        raise ValueError("json_schema.properties must be an object")
    outputs = {name: _output_property(spec) for name, spec in properties.items()}
    outputs["state_handoff_id"] = {"type": "string"}
    return {
        "input_schema": {
            "type": "object",
            "properties": {name: {"type": typed.get(name, "string")} for name in inputs},
            "required": list(inputs),
        },
        "output_schema": {"type": "object", "properties": outputs},
    }


def _structure_errors(error: ValidationError, prefix: str) -> list[RuleViolation]:
    return [
        RuleViolation(rule=1, path=prefix + ".".join(str(p) for p in e["loc"]), message=e["msg"])
        for e in error.errors()
    ]


def _skill_prefix(skill_id: str) -> str | None:
    match = SKILL_ID.match(skill_id)
    return match.group(1) if match else None


def _macro_prefix(macro_category: str) -> str | None:
    match = MACRO_CATEGORY.match(macro_category)
    return match.group(1) if match else None


# --- Rules ----------------------------------------------------------------------------------------


def _check_sub_skill(
    skill: SubSkill, macro_category: str | None, registry: Collection[str]
) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    meta = skill.skill_metadata
    d = skill.isatvon_definition
    i, s, t, v, o, n = (
        d.i_instructions,
        d.s_source,
        d.t_tech_stack,
        d.v_variables,
        d.o_outcome,
        d.n_notification,
    )
    idef = "isatvon_definition."

    # Rule 2: IDs.
    skill_nn = _skill_prefix(meta.skill_id)
    if skill_nn is None:
        found.append((2, "skill_metadata.skill_id", f"{meta.skill_id!r} is not 'MSnn.m_<Name>'"))
    macro_nn = _macro_prefix(meta.macro_category)
    if macro_nn is None:
        found.append(
            (2, "skill_metadata.macro_category", f"{meta.macro_category!r} is not 'MSnn_<Name>'")
        )
    if macro_category is not None and meta.macro_category != macro_category:
        found.append(
            (2, "skill_metadata.macro_category", f"must equal its Agent's {macro_category!r}")
        )
    parent_nn = _macro_prefix(macro_category or meta.macro_category)
    if skill_nn is not None and parent_nn is not None and skill_nn != parent_nn:
        found.append(
            (
                2,
                "skill_metadata.skill_id",
                f"prefix MS{skill_nn} does not match Agent MS{parent_nn}",
            )
        )
    if meta.skill_ref is not None and not SKILL_REF.match(meta.skill_ref):
        found.append((2, "skill_metadata.skill_ref", "must be a Skills master id SK-*"))
    for k, ref in enumerate(meta.rule_refs):
        if not RULE_REF.match(ref):
            found.append((2, f"skill_metadata.rule_refs.{k}", "must be a Rules master id RULE-*"))

    # Rule 3: I/A consistency, rule part only. The LLM-assisted check runs in the isatvon stage.
    for marker in ("Triggered by", "Action:", "Check:", "Bound:"):
        if marker.lower() not in i.statement.lower():
            found.append((3, idef + "i_instructions.statement", f"missing {marker!r}"))
    if not re.search(rf"(?<!\d){v.max_iterations}(?!\d)", i.bound):
        found.append(
            (
                3,
                idef + "i_instructions.bound",
                f"does not mention max_iterations {v.max_iterations}",
            )
        )

    # Rule 4: decoupling.
    if t.action_tool.strip().casefold() == t.fixed_check_tool.strip().casefold():
        found.append(
            (4, idef + "t_tech_stack.fixed_check_tool", "must be independent of action_tool")
        )

    # Rule 5: the five loop bounds.
    low, high = MAX_ITERATIONS_RANGE
    if not low <= v.max_iterations <= high:
        found.append((5, idef + "v_variables.max_iterations", f"must be {low}-{high}"))
    try:
        parse_budget_ms(v.budget_limit)
    except ValueError as exc:
        found.append((5, idef + "v_variables.budget_limit", str(exc)))
    known = set(output_fields(o.json_schema)) | set(s.fresh_state_parameters)
    known |= set(s.required_inputs) | {"max_iterations"}
    for field in ("success_gate", "no_op_condition", "escalation_condition"):
        path = idef + "v_variables." + field
        try:
            names = gate_expr.identifiers(gate_expr.parse(getattr(v, field)))
        except gate_expr.ExpressionError as exc:
            found.append((5, path, str(exc)))
            continue
        for name in sorted(names - known):
            found.append((5, path, f"unresolved identifier {name!r}"))

    # Rule 6: contracts.
    for k, name in enumerate(s.required_inputs):
        if not IDENTIFIER.match(name):
            found.append(
                (6, f"{idef}s_source.required_inputs.{k}", f"{name!r} is not a field name")
            )
    if len(set(s.required_inputs)) != len(s.required_inputs):
        found.append((6, idef + "s_source.required_inputs", "duplicate input names"))
    try:
        build_contracts(skill)
    except ValueError as exc:
        found.append((6, idef + "o_outcome.json_schema", str(exc)))

    # Rule 7: state handoff.
    handoff = idef + "o_outcome.state_handoff_file."
    if o.state_handoff_file.mode != "append_only":
        found.append((7, handoff + "mode", "must be 'append_only'"))
    if o.state_handoff_file.table not in v.tables:
        found.append((7, handoff + "table", "must be listed in v_variables.tables"))
    if not o.state_handoff_file.required_fields:
        found.append((7, handoff + "required_fields", "needs at least 1 field"))

    # Rule 8: next + notifications.
    missing = []
    if len(n.ui_updates) < 2:
        missing.append("at least 2 messages")
    if not any(LOOP_PROGRESS.search(m) for m in n.ui_updates):
        missing.append("an iteration-progress message (Looping [Pass x/y])")
    if not any(END_MESSAGE.search(m) for m in n.ui_updates):
        missing.append("an escalation/completion message")
    if missing:
        found.append((8, idef + "n_notification.ui_updates", "needs " + ", ".join(missing)))
    if n.next is not None:
        if not (SKILL_ID.match(n.next) or MACRO_CATEGORY.match(n.next)):
            found.append((8, idef + "n_notification.next", "must be a skill_id or macro_category"))
        elif n.next in (meta.skill_id, meta.macro_category):
            found.append((8, idef + "n_notification.next", "must not point to itself"))

    # Rule 9: libraries.
    allowed = {_package(p) for p in registry}
    for k, lib in enumerate(t.libraries):
        name = _package(lib.package)
        path = f"{idef}t_tech_stack.libraries.{k}.package"
        if name in FORBIDDEN_LIBRARIES:
            found.append((9, path, f"{lib.package!r} is not allowed (PostgreSQL only)"))
        elif name not in allowed:
            found.append((9, path, f"{lib.package!r} is not in the runtime registry"))

    # Rule 10: secrets are names only.
    for k, secret in enumerate(v.secrets or []):
        if SECRET_VALUE.search(secret) or not SECRET_NAME.match(secret):
            found.append(
                (10, f"{idef}v_variables.secrets.{k}", "must be a secret name, never a value")
            )
    return found


def _package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _violations(found: list[tuple[int, str, str]], prefix: str = "") -> list[RuleViolation]:
    return [RuleViolation(rule=r, path=prefix + p, message=m) for r, p, m in found]


def validate_sub_skill(
    raw: Mapping[str, Any],
    *,
    macro_category: str | None = None,
    library_registry: Collection[str] = DEFAULT_LIBRARY_REGISTRY,
) -> list[RuleViolation]:
    """Run TD §10.4 on one Sub-Skill payload. Empty list = VALID."""
    try:
        skill = SubSkill.model_validate(raw)
    except ValidationError as exc:
        return _structure_errors(exc, "")
    return _violations(_check_sub_skill(skill, macro_category, library_registry))


def validate_agent(
    raw: Mapping[str, Any],
    *,
    workflow_agents: Sequence[str] | None = None,
    library_registry: Collection[str] = DEFAULT_LIBRARY_REGISTRY,
) -> list[RuleViolation]:
    """Run TD §10.4 on an Agent and its Sub-Skills, including the `next` chain.

    `workflow_agents` is every Agent's `macro_category` in Workflow order. With it, the last
    Sub-Skill must point to the following Agent (or `null` for the last Agent); without it, only
    the shape of that last link is checked.
    """
    try:
        agent = Agent.model_validate(raw)
    except ValidationError as exc:
        return _structure_errors(exc, "")
    found: list[tuple[int, str, str]] = []
    if _macro_prefix(agent.macro_category) is None:
        found.append((2, "macro_category", f"{agent.macro_category!r} is not 'MSnn_<Name>'"))
    if agent.agent_ref is not None and not AGENT_REF.match(agent.agent_ref):
        found.append((2, "agent_ref", "must be an Agents master id AG-*"))
    if not agent.sow_refs:
        found.append((10, "sow_refs", "needs at least 1 SOW section"))
    violations: list[RuleViolation] = []
    ids = [s.skill_metadata.skill_id for s in agent.sub_skills]
    for k, skill in enumerate(agent.sub_skills):
        own = _check_sub_skill(skill, agent.macro_category, library_registry)
        violations += _violations(own, f"sub_skills.{k}.")
        if ids.count(skill.skill_metadata.skill_id) > 1:
            found.append((2, f"sub_skills.{k}.skill_metadata.skill_id", "duplicate skill_id"))
    found += _check_chain(agent, workflow_agents)
    return _violations(found) + violations


def _check_chain(agent: Agent, workflow_agents: Sequence[str] | None) -> list[tuple[int, str, str]]:
    """Rule 8: `next` links the Sub-Skills into one linear chain ending at the next Agent."""
    found: list[tuple[int, str, str]] = []
    nexts = {
        s.skill_metadata.skill_id: s.isatvon_definition.n_notification.next
        for s in agent.sub_skills
    }
    index = {skill_id: k for k, skill_id in enumerate(nexts)}

    def path(skill_id: str) -> str:
        return f"sub_skills.{index[skill_id]}.isatvon_definition.n_notification.next"

    inside = {sid: nxt for sid, nxt in nexts.items() if nxt is not None and nxt in nexts}
    for sid, nxt in nexts.items():
        if nxt is not None and SKILL_ID.match(nxt) and nxt not in nexts:
            other = _skill_prefix(nxt) != _macro_prefix(agent.macro_category)
            hint = "another Agent's Sub-Skill; point to that Agent instead" if other else "unknown"
            found.append((8, path(sid), f"{nxt!r} is {hint}"))
    targets = list(inside.values())
    for sid in sorted({t for t in targets if targets.count(t) > 1}):
        found.append((8, path(sid), "more than one Sub-Skill points here (branch)"))
    if found:
        return found
    starts = [sid for sid in nexts if sid not in targets]
    if len(starts) != 1:
        first = next(iter(nexts))
        return [
            (8, path(first), f"Sub-Skills must form one linear chain, found {len(starts)} starts")
        ]
    seen = [starts[0]]
    while seen[-1] in inside:
        seen.append(inside[seen[-1]])
    if len(seen) != len(nexts):
        orphan = next(sid for sid in nexts if sid not in seen)
        return [(8, path(orphan), "next chain has a cycle; Sub-Skill is not reachable")]

    last = seen[-1]
    target = nexts[last]
    if target == agent.macro_category:
        found.append((8, path(last), "must not point to its own Agent"))
    elif workflow_agents is not None:
        order = list(workflow_agents)
        position = order.index(agent.macro_category) if agent.macro_category in order else None
        expected = None
        if position is not None and position + 1 < len(order):
            expected = order[position + 1]
        if position is None:
            found.append((8, "macro_category", "Agent is not in the Workflow"))
        elif target != expected:
            want = "null (last Agent)" if expected is None else repr(expected)
            found.append((8, path(last), f"must be {want}"))
    return found


# --- JSON Schemas ---------------------------------------------------------------------------------


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/isatvon/`."""
    return {
        "agent": Agent.model_json_schema(),
        "sub_skill": SubSkill.model_json_schema(),
        "rule_violation": RuleViolation.model_json_schema(),
    }


def write_schemas(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, schema in schemas().items():
        path = directory / f"{stem}.schema.json"
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_schemas():
        sys.stdout.write(f"wrote {written_path}\n")
