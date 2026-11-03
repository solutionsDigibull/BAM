"""A-02 (#428): the bodies pre-check is an allowlist, so indirection does not get past it.

Each payload is one the audit's PoC (`poc_ast_bypass.py`) got through the denylist; they are
reproduced here from the finding's description. The state_queries payloads (t07-t09) are in
`test_a02_state_queries.py`.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sbam_workers.build.codegen import RenderedSkeleton
from sbam_workers.build.codegen.bodies import BodyFinding, BodyFindingCode, CodeBodies, precheck
from test_codegen_bodies import CASES, GOOD, build, template  # noqa: F401 - the template fixture

RECORDED = Path(__file__).resolve().parents[1] / "llm-mock" / "sbam-codegen"
QUERY = {"q": "SELECT 1 FROM handoff WHERE k = %s"}
EMPTY = RenderedSkeleton(files={}, template=None)  # type: ignore[arg-type]
CODES = BodyFindingCode


def findings(action: str = "return None", helpers: str = "") -> list[BodyFinding]:
    bodies = CodeBodies(
        action_body=action, check_body="return None", state_queries=QUERY, helpers=helpers
    )
    params = {"handoff_table": "handoff", "fresh_state": ["q"]}
    return precheck(bodies, ["json"], params, EMPTY)


def codes_of(found: list[BodyFinding]) -> set[BodyFindingCode]:
    return {f.code for f in found}


def test_a02_t01_a_string_built_getattr_is_rejected() -> None:
    found = findings('x = getattr(__builtins__, "ev" + "al")\nreturn x')
    assert CODES.FORBIDDEN_CALL in codes_of(found)
    assert any("getattr" in f.message for f in found)


def test_a02_t02_open_is_rejected() -> None:
    found = findings('return open("/etc/passwd").read()')
    assert codes_of(found) == {CODES.FORBIDDEN_CALL}
    assert found[0].line == 1  # the position inside the body, not the wrapped file


def test_a02_t03_a_class_walk_without_subclasses_is_rejected() -> None:
    found = findings("return ().__class__.__base__.__mro__[-1]")
    assert codes_of(found) == {CODES.PRIVATE_ATTRIBUTE}
    assert len(found) == 3  # __class__, __base__, __mro__: each one is named


def test_a02_t04_globals_is_rejected_even_when_the_body_rebinds_the_name() -> None:
    assert CODES.FORBIDDEN_CALL in codes_of(findings("return globals()"))
    rebind = findings("globals = globals\nreturn globals()", helpers="g = globals\n")
    assert CODES.FORBIDDEN_CALL in codes_of(rebind)  # not an "is it bound here" exemption


def test_a02_t05_unbounded_iteration_is_rejected() -> None:
    forever = findings(
        "for _ in iter(int, 1):\n    pass\nreturn None"
    )  # never raises StopIteration
    assert CODES.FORBIDDEN_CALL in codes_of(forever)
    huge = findings("total = 0\nfor i in range(10 ** 12):\n    total += i\nreturn total")
    assert codes_of(huge) == {CODES.RESOURCE_BOMB}


def test_a02_t06_a_memory_bomb_is_rejected() -> None:
    assert codes_of(findings('return "x" * (10 ** 10)')) == {CODES.RESOURCE_BOMB}
    assert codes_of(findings("return [0] * 5_000_000")) == {CODES.RESOURCE_BOMB}
    assert codes_of(findings("return 1 << (10 ** 9)")) == {CODES.RESOURCE_BOMB}


@pytest.mark.parametrize(
    "body",
    [
        "class Evil:\n    pass\nreturn Evil",  # classes
        "global X\nX = 1\nreturn X",
        "x = 1\ndel x\nreturn None",
        "def gen():\n    yield 1\nreturn gen",
    ],
)
def test_a02_constructs_a_tool_body_does_not_need_are_refused(body: str) -> None:
    assert CODES.DISALLOWED_SYNTAX in codes_of(findings(body))


def test_a02_interpreter_internals_without_an_underscore_are_refused() -> None:
    assert CODES.PRIVATE_ATTRIBUTE in codes_of(findings("g = (x for x in [1])\nreturn g.gi_frame"))


def test_a02_a_small_repeat_or_range_is_fine() -> None:
    assert findings('return "-" * 40 + str(sum(range(100)))') == []


def test_a02_the_message_names_the_construct_not_the_body() -> None:
    found = findings('SECRET = "customer-token-123"\nreturn getattr(SECRET, "x")')
    assert all("customer-token-123" not in f.message for f in found)


@pytest.mark.parametrize("name", GOOD)
def test_a02_every_recorded_legitimate_answer_still_passes(
    template: Any, name: str  # noqa: F811
) -> None:
    """The recorded `sbam-codegen` answers (MS01.1, the stock-reserve and edge-enum cases) are what
    a correct body looks like: the allowlist must not reject them."""
    from sbam_workers.build.codegen.bodies import import_allowlist

    built = build(template, name)
    path = RECORDED / f"e11_2_{name}.json"
    answer = json.loads(path.read_text(encoding="utf-8"))["response"]
    bodies = CodeBodies(**answer)
    allowed = import_allowlist(template.base_imports, built.modules)
    assert precheck(bodies, allowed, built.params, built.skeleton) == []
