"""E13.4 / #620: the test-generation prompt says what the validator accepts, so it cannot drift.

The model's answer is validated by `sbam_api.testgen.validate` and a refused case is re-asked with
its position only, so the prompt is the one place the model can learn the rules. These tests tie the
rules the prompt states to the code that enforces them.
"""

import re

from sbam_api.testgen.seed_sql import SeedRejected, check_statement
from sbam_contracts.promptfoo import AssertionType
from sbam_contracts.testgen import MAX_SEED_ROWS
from sbam_workers.llm.registry import PromptRegistry

PROMPT = PromptRegistry().get("testgen")


def test_e13_4_the_newest_testgen_prompt_is_1_1_0_and_1_0_0_is_untouched() -> None:
    registry = PromptRegistry()
    assert PROMPT.version == "1.1.0"
    assert registry.get("testgen", "1.0.0").sha256 == (
        "9e0764c14c84d3ebed0b6623c584b4cb052043dbd4bf2cf9345a269c964f1385"
    )  # a released version is never edited


def test_e13_4_every_assertion_type_the_prompt_names_is_one_the_validator_knows() -> None:
    section = PROMPT.text.split("- `assertions`:")[1].split("\n- ")[0]
    named = re.findall(r"`([a-z_]+)` \(value:", section)
    assert named, "the prompt should name the types it allows"
    known = {t.value for t in AssertionType}
    assert set(named) <= known


def test_e13_4_the_assertion_types_the_prompt_allows_are_the_ones_that_take_a_value() -> None:
    section = PROMPT.text.split("- `assertions`:")[1].split("\n- ")[0]
    named = set(re.findall(r"`([a-z_]+)` \(value:", section))
    # The suite adds every other deterministic check to each case itself (suite.py), so the model is
    # told to leave them out; only these three carry something the model supplies.
    assert named == {"expected_output", "ui_updates", "llm_rubric"}
    assert "leave this an empty list" in section


def test_e13_4_the_row_limit_the_prompt_states_is_the_real_one() -> None:
    assert f"at most {MAX_SEED_ROWS} rows" in PROMPT.text


def test_e13_4_what_the_prompt_says_is_refused_is_refused() -> None:
    tables = ["orders"]
    for refused in (
        "INSERT INTO public.orders (id) VALUES (1)",  # schema-qualified
        "INSERT INTO orders (created) VALUES (NOW())",  # a function
        "INSERT INTO orders (created) VALUES (CURRENT_DATE)",
        "INSERT INTO orders (id) VALUES (gen_random_uuid())",
        "INSERT INTO orders (id) SELECT id FROM other",  # a subquery
        "INSERT INTO orders (id) VALUES (1) RETURNING id",
        "DELETE FROM orders",
        "CREATE TABLE x (id int)",
        "INSERT INTO orders (id) VALUES (1); INSERT INTO orders (id) VALUES (2)",  # two statements
        "INSERT INTO elsewhere (id) VALUES (1)",  # a table the Sub-Skill does not declare
    ):
        try:
            check_statement(refused, tables)
        except SeedRejected:
            continue
        raise AssertionError(f"the prompt says this is refused, but it was accepted: {refused}")


def test_e13_4_what_the_prompt_says_is_allowed_is_allowed() -> None:
    tables = ["orders"]
    for allowed in (
        "INSERT INTO orders (id, status) VALUES (1, 'NEW')",
        "INSERT INTO orders (id, placed) VALUES (2, '2026-01-01'::date)",  # a cast of a literal
        "UPDATE orders SET status = 'PAID' WHERE id = 1",
    ):
        assert check_statement(allowed, tables) >= 0
