"""E13.2 activity rules that need no database: which infrastructure failures are worth a retry, how
much
room a Sub-Skill's loop gets, what makes a dataset runnable and how the mock API is found."""

import uuid

import pytest
from sbam_contracts.build import TESTS_FAILED
from sbam_contracts.sandbox import InfraReason, SandboxLimits
from sbam_contracts.testgen import CaseBody, CaseKind
from sbam_workers.sandbox import activity as stage
from sbam_workers.sandbox.activity import (
    NOT_RETRYABLE,
    SANDBOX_UNAVAILABLE,
    limits_for,
    mock_api_of,
    unavailable,
)
from sbam_workers.sandbox.load import INPUT_MISMATCH, Identity, SqlSandboxInputs, hex_of, mismatch
from sbam_workers.settings import WorkerSettings
from temporalio.exceptions import ApplicationError

IDENTITY = Identity(uuid.uuid4(), uuid.uuid4(), "sha256:" + "a" * 64)


def body(name: str) -> CaseBody:
    return CaseBody.model_validate({"name": name, "input": {}, "expected_terminal": "SUCCESS"})


@pytest.mark.parametrize("reason", list(InfraReason))
def test_e13_2_only_failures_that_a_retry_cannot_change_are_final(reason: InfraReason) -> None:
    error = unavailable(reason)
    assert error.type == SANDBOX_UNAVAILABLE
    assert error.non_retryable is (reason in NOT_RETRYABLE)


def test_e13_2_the_final_reasons_are_exactly_the_ones_a_retry_cannot_fix() -> None:
    assert NOT_RETRYABLE == {
        InfraReason.HASH_MISMATCH,
        InfraReason.INPUT_MISMATCH,
        InfraReason.DATASET_INVALID,
        InfraReason.TEMPLATE_DIGEST_MISMATCH,
    }
    for reason in (
        InfraReason.HOST_UNREACHABLE,
        InfraReason.VM_CREATE_FAILED,
        InfraReason.NOT_CONFIGURED,
    ):
        assert not unavailable(reason).non_retryable  # a host that comes back must be retried


def test_e13_2_a_test_failure_is_final_and_names_the_attempt_the_corrector_reads() -> None:
    error = stage.loop_tests_failed(IDENTITY)
    assert error.type == TESTS_FAILED and error.non_retryable
    assert error.details[0] == [str(IDENTITY.attempt_id)]


def test_e13_2_an_input_mismatch_is_final() -> None:
    error = mismatch("a gate did not pass")
    assert error.type == INPUT_MISMATCH and error.non_retryable


@pytest.mark.parametrize(
    ("iterations", "budget_ms", "expected"),
    [(3, 5000, 60), (1, 1000, 60), (10, 5000, 110), (29, 5000, 300)],
    ids=["ms01_1", "tiny", "wide", "at the ceiling"],
)
def test_e13_2_the_case_limit_covers_the_declared_loop_and_never_goes_below_the_default(
    iterations: int, budget_ms: int, expected: int
) -> None:
    limits = limits_for({"max_iterations": iterations, "budget_ms": budget_ms})
    assert limits.case_timeout_s == expected


def test_e13_2_a_loop_that_needs_more_than_the_policy_allows_is_a_final_input_mismatch() -> None:
    with pytest.raises(ApplicationError) as refused:
        limits_for({"max_iterations": 30, "budget_ms": 5000})  # 310 s: one second over the ceiling
    assert refused.value.type == INPUT_MISMATCH and refused.value.non_retryable
    assert SandboxLimits().case_timeout_s == 60  # the default is untouched


def test_e13_2_a_dataset_must_be_synthetic_and_have_the_loops_scenarios() -> None:
    check = SqlSandboxInputs._dataset_is_runnable
    good = [
        (CaseKind.PASS_FIRST, body("a")),
        (CaseKind.ESCALATION, body("b")),
        (CaseKind.EDGE, body("c")),
    ]
    check(good, True)
    with pytest.raises(ApplicationError) as not_synthetic:
        check(good, False)
    assert not_synthetic.value.type == INPUT_MISMATCH
    for bad in (good[:2], [good[0], good[2], (CaseKind.EDGE, body("d"))]):  # too few; no ESCALATION
        with pytest.raises(ApplicationError) as refused:
            check(bad, True)
        assert refused.value.type == INPUT_MISMATCH


def test_e13_2_hashes_compare_with_or_without_the_prefix() -> None:
    assert hex_of("sha256:" + "a" * 64) == hex_of("a" * 64)


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> WorkerSettings:
    monkeypatch.setenv("TEMPORAL_HOST", "t:1")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    for key in ("MOCK_API_URL", "MOCK_API_IP"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return WorkerSettings()  # type: ignore[call-arg]


def test_e13_2_the_mock_api_is_only_offered_when_the_operator_pinned_its_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert mock_api_of(settings(monkeypatch)) is None
    assert mock_api_of(settings(monkeypatch, MOCK_API_URL="http://sbam-mock-api:8080")) is None
    found = mock_api_of(
        settings(monkeypatch, MOCK_API_URL="http://sbam-mock-api:8080", MOCK_API_IP="10.1.2.3")
    )
    assert found is not None and (found.host, found.ip, found.port) == (
        "sbam-mock-api",
        "10.1.2.3",
        8080,
    )


class TamperedStorage:
    def get(self, key: str, *, verify: bool = False) -> bytes:
        from sbam_api.storage.base import StorageIntegrityError

        if verify:
            raise StorageIntegrityError()
        return b"x"


def test_e13_2_a_dataset_file_that_no_longer_hashes_to_its_key_is_a_hash_mismatch() -> None:
    """A tampered stored object is a security event (no VM, non-retryable), not a flaky read."""
    from sbam_workers.sandbox.verify import HashMismatch

    inputs = SqlSandboxInputs(None, TamperedStorage())  # type: ignore[arg-type]
    with pytest.raises(HashMismatch) as caught:
        inputs._verified("t/p/tests/" + "a" * 64, "a dataset file")
    assert caught.value.reason is InfraReason.HASH_MISMATCH
    assert "a dataset file" in str(caught.value) and "t/p" not in str(caught.value)
