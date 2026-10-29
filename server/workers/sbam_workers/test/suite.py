"""The Promptfoo suite of one Sub-Skill build (E13.6, TD §15.1), built from data and nothing else.

Inputs are the Sub-Skill's ISATVON (its contracts, the V bounds, the handoff table's required
fields) and the dataset's cases. Output is a `Suite` whose canonical hash (`suite_sha256`) is the
same for the same inputs, so certification can tell whether a stored result still applies.

Every case gets the deterministic assertions first: the terminal state, the passes within
`max_iterations`, every cycle within `budget_limit`, one handoff row per pass with each required
field, and the `Looping [Pass x/y]` updates in order. A case that ends in SUCCESS also gets the
output schema, `state_handoff_id`, and the expected outputs when the case declares them.

Assertions a case carries (FDE or model) are kept when their type is one the suite knows; any other
type is refused, so a typo cannot turn into a test that checks nothing. An `llm_rubric` is added
only when every output field is free text (no enum or format), as the last assertion, and only to a
SUCCESS case: a rubric is never the only check.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.isatvon14 import build_contracts, parse_budget_ms
from sbam_contracts.promptfoo import (
    AssertionType,
    Suite,
    SuiteAssertion,
    SuiteBounds,
    SuiteCase,
)

HANDOFF_ID: Final = "state_handoff_id"
_BASE: Final = (
    AssertionType.TERMINAL_STATE,
    AssertionType.ITERATIONS_WITHIN_BOUND,
    AssertionType.CYCLE_TIME_WITHIN_BUDGET,
    AssertionType.HANDOFF_ROWS,
    AssertionType.UI_UPDATES,
)
_ON_SUCCESS: Final = (AssertionType.OUTPUT_SCHEMA, AssertionType.STATE_HANDOFF_ID)
_FREE_TEXT_KEYS: Final = frozenset({"enum", "format", "const", "pattern"})


class SuiteBuildError(ValueError):
    """The suite cannot be built from this Sub-Skill or dataset (the message names what)."""


def output_schema_of(isatvon: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest's `contracts.output_schema`, with every declared output required."""
    schema: dict[str, Any] = build_contracts(SubSkillModel.model_validate(isatvon))["output_schema"]
    properties = schema.get("properties", {})
    if HANDOFF_ID not in properties:
        raise SuiteBuildError(f"output_schema has no {HANDOFF_ID}")
    return {**schema, "required": sorted(properties)}


def bounds_of(isatvon: Mapping[str, Any]) -> SuiteBounds:
    definition = SubSkillModel.model_validate(isatvon).isatvon_definition
    try:
        budget = parse_budget_ms(definition.v_variables.budget_limit)
    except ValueError as exc:
        raise SuiteBuildError(f"budget_limit: {exc}") from exc
    return SuiteBounds(
        max_iterations=definition.v_variables.max_iterations,
        budget_ms=budget,
        handoff_fields=list(definition.o_outcome.state_handoff_file.required_fields),
    )


def free_text_only(schema: Mapping[str, Any]) -> bool:
    """True when every declared output (besides the handoff id) is a string with no enum or format:
    the one case deterministic checks cannot judge the content of."""
    fields = {k: v for k, v in schema.get("properties", {}).items() if k != HANDOFF_ID}
    return bool(fields) and all(
        isinstance(spec, Mapping)
        and spec.get("type") == "string"
        and not _FREE_TEXT_KEYS & set(spec)
        for spec in fields.values()
    )


def _assertions(
    case: Mapping[str, Any], free_text: bool, rubric: str | None
) -> list[SuiteAssertion]:
    terminal = str(case["expected_terminal"])
    wanted = [*_BASE, *(_ON_SUCCESS if terminal == "SUCCESS" else ())]
    out = [SuiteAssertion(type=t) for t in wanted]
    declared = case.get("expected_output")
    if declared:
        out.append(SuiteAssertion(type=AssertionType.EXPECTED_OUTPUT, value=declared))
    have = {a.type for a in out}
    for extra in case.get("assertions") or []:
        try:
            kind = AssertionType(str(extra["type"]))
        except ValueError:
            raise SuiteBuildError(
                f"case {case['name']!r}: unknown assertion type {extra['type']!r}"
            ) from None
        if kind is AssertionType.LLM_RUBRIC or kind in have:
            continue  # the rubric is decided below; a repeat adds nothing
        out.append(SuiteAssertion(type=kind, value=extra.get("value")))
        have.add(kind)
    if free_text and terminal == "SUCCESS" and rubric:
        out.append(SuiteAssertion(type=AssertionType.LLM_RUBRIC, value=rubric))
    return out


def build_suite(
    isatvon: Mapping[str, Any], cases: Sequence[Mapping[str, Any]], rubric: str | None = None
) -> Suite:
    """`cases`: name, kind, input, expected_terminal, optional expected_output and assertions."""
    schema = output_schema_of(isatvon)
    free_text = free_text_only(schema)
    return Suite(
        bounds=bounds_of(isatvon),
        output_schema=schema,
        cases=[
            SuiteCase(
                name=str(c["name"]),
                kind=c["kind"],
                input=dict(c["input"]),
                expected_terminal=c["expected_terminal"],
                assertions=_assertions(c, free_text, rubric),
            )
            for c in cases
        ],
    )
