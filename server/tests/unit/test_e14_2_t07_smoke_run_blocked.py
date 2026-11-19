"""E14.2-T07 (#130): "smoke run failure at node 3 -> WORKFLOW_VALIDATION_FAILED; the notification
names node 3". Blocked, and recorded here so the gap can't be forgotten.

The test needs a Workflow smoke-run activity: the Skill chain run in DAG order through the loop
harness in CubeSandbox, on synthetic data, under the Sub-Skill egress policy. None exists
(`certify/workflow.py` still carries the TODO). Building it needs a ProjectPipeline hook between
`validate_workflow` and `certify_workflow` (D3, `pipeline/**`); the design question is on #130.

This strict xfail turns into a failure the day such an activity is registered on the CERTIFY
queue. Then replace it with the real T07 test: an integration test that skips unless
`SANDBOX_MODE=CUBESANDBOX`, like `test_e13_6_e14_1_cubesandbox.py`.
"""

import pytest
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry


def _certify_activity_names() -> set[str]:
    registry.load_all()
    _, activities = registry.collect(TaskQueue.CERTIFY)
    names = set()
    for fn in activities:
        defn = getattr(fn, "__temporal_activity_definition", None)
        names.add(getattr(defn, "name", None) or fn.__name__)
    return names


def test_e14_2_t07_the_certify_queue_has_real_activities() -> None:
    """Control: the lookup below really sees the certify activities, so the xfail is not vacuous."""
    assert {"certify_macros", "validate_workflow", "certify_workflow"} <= _certify_activity_names()


@pytest.mark.xfail(
    strict=True,
    reason="E14.2-T07 blocked: no Workflow smoke-run activity exists yet (needs a D3 pipeline "
    "hook; question on #130). When this XPASSes, write the real T07 test.",
)
def test_e14_2_t07_a_workflow_smoke_run_activity_is_registered() -> None:
    assert any("smoke" in name for name in _certify_activity_names())
