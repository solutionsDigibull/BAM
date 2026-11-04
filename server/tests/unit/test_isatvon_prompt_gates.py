"""#679: the gate examples in the Sub-Skill prompt agree with the gate grammar.

The model wrote `output['status'] == 'Valid'` for every gate because the prompt never said that a
field is a bare name. The newest `isatvon-sub-skills` prompt now lists `Right:` and `Wrong:`
examples; this parses each one with `gate_expr`, so the prompt cannot drift from the grammar.
"""

import re

import pytest
from sbam_contracts import gate_expr
from sbam_workers.llm.registry import PromptRegistry

PROMPT = PromptRegistry().get("isatvon-sub-skills")
RIGHT = re.findall(r"Right: `([^`]+)`", PROMPT.text)
WRONG = re.findall(r"Wrong: `([^`]+)`", PROMPT.text)


def test_679_the_prompt_lists_right_and_wrong_gate_examples() -> None:
    assert len(RIGHT) >= 2
    assert len(WRONG) >= 3
    assert "bare name" in PROMPT.text


@pytest.mark.parametrize("text", RIGHT)
def test_679_every_right_example_parses(text: str) -> None:
    gate_expr.parse(text)


@pytest.mark.parametrize("text", WRONG)
def test_679_every_wrong_example_is_rejected(text: str) -> None:
    with pytest.raises(gate_expr.ExpressionError):
        gate_expr.parse(text)


def test_679_the_subscript_the_model_wrote_is_rejected_with_its_code() -> None:
    with pytest.raises(gate_expr.ExpressionError) as caught:
        gate_expr.parse("output['status'] == 'Valid'")
    assert caught.value.code is gate_expr.ErrorCode.SUBSCRIPT_NOT_ALLOWED
