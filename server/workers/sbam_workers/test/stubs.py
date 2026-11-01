"""Test stages of SubSkillBuild (owners: testgen / sandbox issues, #107 runs them).

Typed handles: fixed activity names and Pydantic in/out (sbam_contracts.build). The real stages are
registered elsewhere; `run_sandbox_tests` is `sandbox/activity.py`. It raises `TestsFailed`
(non-retryable, the code attempt as details) when the tests ran and failed, and any other error for
sandbox infrastructure trouble (retried by Temporal, then TESTING_BLOCKED, never `correct`).
"""

from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import IdResult
from temporalio import activity

from sbam_workers.stub_ids import stage_stub_id


@activity.defn(
    name="generate_tests"
)  # registered by `build/wiring.py`; the workflow's typed handle
async def generate_tests(inp: BuildStageInput) -> IdResult:
    """Real (E13.4): `sbam-testgen` writes the Sub-Skill's tests."""
    return IdResult(id=stage_stub_id(inp, "tests"))


@activity.defn(name="build_promptfoo_suite")  # registered by `build/wiring.py`; a typed handle
async def build_promptfoo_suite(inp: BuildStageInput) -> IdResult:
    return IdResult(id=stage_stub_id(inp, "promptfoo_suite"))


@activity.defn(name="run_sandbox_tests")  # registered by `sandbox/activity.py`; a typed handle
async def run_sandbox_tests(inp: BuildStageInput) -> IdResult:
    """Real (E13.2): runs the tests in the sandbox, heartbeating (TD §13.3: 20 min, 3 attempts)."""
    return IdResult(id=stage_stub_id(inp, "sandbox_run"))
