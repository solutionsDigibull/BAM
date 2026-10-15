"""Test stages of SubSkillBuild (owners: testgen / sandbox issues, #107 runs them).

STUBS: fixed activity names and Pydantic in/out (sbam_contracts.build). `run_sandbox_tests` raises
`TestsFailed` (non-retryable, finding ids as details) when the tests ran and failed, and any other
error for sandbox infrastructure trouble (retried by Temporal, then TESTING_BLOCKED, never
`correct`).
"""

from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stage_stub_id


@activity_for(TaskQueue.LLM)
@activity.defn(name="generate_tests")
async def generate_tests(inp: BuildStageInput) -> IdResult:
    """Real: LLM (`sbam-testgen`) writes the Sub-Skill's tests."""
    return IdResult(id=stage_stub_id(inp, "tests"))


@activity_for(TaskQueue.TEST)
@activity.defn(name="build_promptfoo_suite")
async def build_promptfoo_suite(inp: BuildStageInput) -> IdResult:
    return IdResult(id=stage_stub_id(inp, "promptfoo_suite"))


@activity_for(TaskQueue.TEST)
@activity.defn(name="run_sandbox_tests")
async def run_sandbox_tests(inp: BuildStageInput) -> IdResult:
    """Real: runs the tests in the sandbox, heartbeating (TD §13.3: 20 min, 3 attempts)."""
    return IdResult(id=stage_stub_id(inp, "sandbox_run"))
