"""E13.2 the errors of `run_sandbox_tests` through the real `SubSkillBuild` workflow.

The activity is a stand-in that raises exactly what the real one raises (`unavailable`,
`loop_tests_failed` and the hash mismatch of `sbam_workers.sandbox.activity`), so what is checked is
the contract between the two: which failures Temporal retries, which park the Sub-Skill in
TESTING_BLOCKED and which spend a correction. The real activity's own behaviour on a dead host is in
tests/integration/test_e13_2_activity_stack.py."""

import uuid
from typing import Any

import pytest
from sbam_contracts.build import BuildStageInput, CorrectionInput
from sbam_contracts.sandbox import InfraReason
from sbam_workers.sandbox.activity import HASH_MISMATCH, loop_tests_failed, unavailable
from sbam_workers.sandbox.load import Identity
from temporalio.exceptions import ApplicationError
from test_sub_skill_build import Build, run, traced

IDENTITY = Identity(uuid.uuid4(), uuid.uuid4(), "sha256:" + "a" * 64)


def blocked_run(
    error: ApplicationError,
) -> tuple[Any, list[BuildStageInput], list[CorrectionInput], list[str]]:
    sandbox: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []
    down = {"on": True}

    def behave(inp: Any) -> None:
        if down["on"]:
            raise error

    async def scenario(build: Build) -> tuple[Any, list[str]]:
        blocked = await build.until_waiting()
        down["on"] = False
        await build.retry()
        await build.finish()
        return blocked, build.actions()

    blocked, actions = run(
        scenario,
        run_sandbox_tests=traced("run_sandbox_tests", sandbox, behave),
        correct=traced("correct", corrections),
    )
    return blocked, sandbox, corrections, actions


@pytest.mark.parametrize(
    "reason",
    [InfraReason.HOST_UNREACHABLE, InfraReason.VM_CREATE_FAILED, InfraReason.NOT_CONFIGURED],
)
def test_e13_2_t03_a_sandbox_that_is_down_is_retried_three_times_then_blocks_testing(
    reason: InfraReason,
) -> None:
    blocked, sandbox, corrections, actions = blocked_run(unavailable(reason))
    assert blocked.state.value == "TESTING_BLOCKED" and blocked.corrections_used == 0
    assert len(sandbox) == 3 + 1  # three infrastructure attempts, then the FDE's retry
    assert corrections == []  # no correction attempt was consumed
    assert actions[:3] == ["tests_started", "sandbox_unavailable", "sub_skill.testing_blocked"]


@pytest.mark.parametrize(
    "error",
    [
        unavailable(InfraReason.DATASET_INVALID),
        unavailable(InfraReason.TEMPLATE_DIGEST_MISMATCH),
        unavailable(InfraReason.INPUT_MISMATCH),
        ApplicationError("an input changed", type=HASH_MISMATCH, non_retryable=True),
    ],
    ids=["dataset", "template", "input", "hash"],
)
def test_e13_2_a_failure_no_retry_can_fix_blocks_testing_after_one_attempt(
    error: ApplicationError,
) -> None:
    blocked, sandbox, corrections, _ = blocked_run(error)
    assert blocked.state.value == "TESTING_BLOCKED" and blocked.corrections_used == 0
    assert len(sandbox) == 1 + 1  # one attempt (not retryable), then the FDE's retry
    assert corrections == []


def test_e13_2_a_test_failure_spends_a_correction_and_hands_the_corrector_the_attempt() -> None:
    sandbox: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []

    def behave(inp: Any) -> None:
        if inp.attempt == 0:
            raise loop_tests_failed(IDENTITY)

    async def scenario(build: Build) -> Any:
        return await build.finish()

    result = run(
        scenario,
        run_sandbox_tests=traced("run_sandbox_tests", sandbox, behave),
        correct=traced("correct", corrections),
    )
    assert result.certified and len(sandbox) == 2  # attempt 0 failed once, never retried as infra
    assert corrections[0].failed_gate == "run_sandbox_tests"
    assert corrections[0].finding_ids == [IDENTITY.attempt_id]
