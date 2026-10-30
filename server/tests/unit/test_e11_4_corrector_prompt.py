"""E11.4 / #631: the corrector prompt's rules are the ones the pre-check enforces.

`correct-code/1.1.0` tells the model how to repair a `MANUAL_LOOP` finding, because in the E15.5
gate the corrector often said it had removed a `while` loop and left it in. These tests tie what
the prompt says to the code that decides whether a repair is accepted.
"""

import re

from sbam_workers.build.codegen.bodies import BodyFindingCode, CodeBodies, precheck
from sbam_workers.evals.correction_set import build_env
from sbam_workers.evals.golden import correction_base
from sbam_workers.llm.registry import PromptRegistry

REGISTRY = PromptRegistry()
PROMPT = REGISTRY.get("correct-code")
BASE = correction_base()
ENV = build_env(BASE)


def codes(**over: str) -> set[str]:
    bodies = CodeBodies.model_validate({**BASE["bodies"], **over})
    return {f.code.value for f in precheck(bodies, ENV.allowlist, ENV.params, ENV.skeleton)}


def test_e11_4_the_newest_corrector_prompt_is_1_1_0_and_1_0_0_is_untouched() -> None:
    assert PROMPT.version == "1.1.0"
    assert REGISTRY.get("correct-code", "1.0.0").sha256 == (
        "5013b740498265f3bb9b79b65aa8c22725cdc1eb3296ea18a3d1e5778cb641c7"
    )  # a released version is never edited


def test_e11_4_every_finding_code_the_prompt_names_is_a_real_pre_check_code() -> None:
    named = set(re.findall(r"`([A-Z][A-Z_]+)` finding", PROMPT.text))
    assert named == {"MANUAL_LOOP"}
    assert named <= {c.value for c in BodyFindingCode}


def test_e11_4_the_prompt_tells_the_model_to_delete_a_flagged_loop_not_reshape_it() -> None:
    text = " ".join(PROMPT.text.split())
    assert "Delete the loop and keep one pass of what was inside it" in text
    assert "do not change its condition" in text
    assert "Say only what the code you return does" in text
    assert "no `while`, forbidden call or forbidden import is left" in text


def test_e11_4_what_the_prompt_says_the_pre_check_refuses_it_refuses() -> None:
    body = BASE["bodies"]["check_body"]
    assert "MANUAL_LOOP" in codes(check_body="while True:\n    break\n" + body)
    assert "FORBIDDEN_CALL" in codes(check_body='x = eval("1")\n' + body)
    assert "FORBIDDEN_IMPORT" in codes(helpers="import pickle\n\n" + BASE["bodies"]["helpers"])


def test_e11_4_a_loop_replaced_by_one_pass_is_accepted() -> None:
    """The repair the prompt asks for: the loop is gone and its body runs once."""
    action = BASE["bodies"]["action_body"]
    looped = "attempts = 0\nwhile attempts < 3:\n    attempts += 1\n" + action
    once = "attempts = 1\n" + action
    assert "MANUAL_LOOP" in codes(action_body=looped)
    assert codes(action_body=once) == set()
