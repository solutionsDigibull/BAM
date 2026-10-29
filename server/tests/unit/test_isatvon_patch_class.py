"""#446 classifier: what a corrector's ISATVON patch did, from its operations alone. Pure."""

import pytest
from sbam_contracts.build import MAX_CHANGED_PATHS, IsatvonPatchClass
from sbam_workers.build.codegen.correction import (
    MAX_CHANGED_PATH_CHARS,
    PatchOp,
    classify_patch,
)

NONE, NEUTRAL, BEHAVIOUR = (
    IsatvonPatchClass.NONE,
    IsatvonPatchClass.NEUTRAL,
    IsatvonPatchClass.BEHAVIOUR,
)
ROOT = "/isatvon_definition"
LIBS = f"{ROOT}/t_tech_stack/libraries"


def op(kind: str, path: str, value: object = None) -> PatchOp:
    return PatchOp(op=kind, path=path, value=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ops", "expected"),
    [
        pytest.param([], NONE, id="no-ops"),
        pytest.param([op("test", f"{ROOT}/v_variables/max_iterations", 3)], NONE, id="test-only"),
        # the libraries a Sub-Skill imports: a missing library is fixed this way
        pytest.param([op("add", f"{LIBS}/-", {"package": "httpx"})], NEUTRAL, id="library-added"),
        pytest.param([op("remove", f"{LIBS}/2")], NEUTRAL, id="library-removed"),
        pytest.param([op("replace", f"{LIBS}/0/version_constraint", ">=1")], NEUTRAL, id="pin"),
        pytest.param([op("replace", LIBS, [{"package": "httpx"}])], NEUTRAL, id="whole-list"),
        pytest.param(
            [op("test", f"{ROOT}/v_variables/max_iterations", 3), op("add", f"{LIBS}/-", {})],
            NEUTRAL,
            id="a-test-does-not-make-it-behaviour",
        ),
        # what the Sub-Skill does: a bound, a gate, a schema, the tools, the routing
        pytest.param(
            [op("replace", f"{ROOT}/v_variables/max_iterations", 9)], BEHAVIOUR, id="bound"
        ),
        pytest.param(
            [op("replace", f"{ROOT}/v_variables/success_gate", "true")], BEHAVIOUR, id="gate"
        ),
        pytest.param(
            [op("replace", f"{ROOT}/t_tech_stack/gateway_routing", "x")], BEHAVIOUR, id="routing"
        ),
        pytest.param(
            [op("replace", f"{ROOT}/o_outcome/state_handoff_file", "x")], BEHAVIOUR, id="outcome"
        ),
        pytest.param(
            [op("replace", f"{ROOT}/t_tech_stack", {})], BEHAVIOUR, id="the-whole-tech-stack"
        ),
        # one operation outside the neutral subtree makes the whole patch BEHAVIOUR
        pytest.param(
            [op("add", f"{LIBS}/-", {}), op("replace", f"{ROOT}/v_variables/max_iterations", 9)],
            BEHAVIOUR,
            id="mixed",
        ),
        # near misses are not the libraries subtree
        pytest.param(
            [op("replace", f"{ROOT}/t_tech_stack/libraries_extra", 1)],
            BEHAVIOUR,
            id="prefix-of-a-name",
        ),
        pytest.param(
            [op("replace", f"{ROOT}/t_tech_stack/fixed_check_tool/libraries", 1)],
            BEHAVIOUR,
            id="deeper-name",
        ),
        pytest.param(
            [op("replace", "isatvon_definition/t_tech_stack/libraries/0", 1)],
            BEHAVIOUR,
            id="not-a-pointer",
        ),
    ],
)
def test_a20_the_class_of_a_patch(ops: list[PatchOp], expected: IsatvonPatchClass) -> None:
    assert classify_patch(ops)[0] is expected


def test_a20_the_changed_paths_are_unique_ordered_and_leave_out_tests() -> None:
    ops = [
        op("test", f"{ROOT}/v_variables/max_iterations", 3),
        op("replace", f"{ROOT}/v_variables/max_iterations", 9),
        op("replace", f"{ROOT}/v_variables/budget_limit", "5s"),
        op("replace", f"{ROOT}/v_variables/max_iterations", 8),  # the same pointer again
    ]
    kind, paths = classify_patch(ops)
    assert kind is BEHAVIOUR
    assert paths == (f"{ROOT}/v_variables/max_iterations", f"{ROOT}/v_variables/budget_limit")


def test_a20_the_paths_fit_the_contract() -> None:
    """Every path is cut to what `CorrectionResult.changed_paths` accepts, and there are at most
    that many, however many operations the patch had."""
    long = f"{ROOT}/" + "x" * 400
    kind, paths = classify_patch([op("replace", long, 1)])
    assert kind is BEHAVIOUR and len(paths) == 1 and len(paths[0]) == MAX_CHANGED_PATH_CHARS
    many = [op("replace", f"{ROOT}/v_variables/p{i}", i) for i in range(MAX_CHANGED_PATHS + 10)]
    assert len(classify_patch(many)[1]) == MAX_CHANGED_PATHS
