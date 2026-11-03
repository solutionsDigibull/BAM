"""E14.1: the Sub-Skill certification checklist, rule by rule."""

from dataclasses import replace

from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.promptfoo import TestRunState
from sbam_contracts.sandbox import SandboxMode
from sbam_contracts.testgen import CaseKind
from sbam_workers.certify.checklist import (
    REQUIRED_GATES,
    Evidence,
    GateEvidence,
    SandboxEvidence,
    TestEvidence,
    evaluate,
)

ATTEMPT = "attempt-1"
BUNDLE = "b" * 64
DATASET = "d" * 64
SUITE = "5" * 64
RUNTIME = "sha256:" + "r" * 64


def gate(name: GateName, **kw: object) -> GateEvidence:
    values: dict[str, object] = {
        "gate": name,
        "status": GateStatus.PASSED,
        "code_attempt_id": ATTEMPT,
        "bundle_sha256": BUNDLE,
    }
    return GateEvidence(**{**values, **kw})  # type: ignore[arg-type]


def evidence(**kw: object) -> Evidence:
    base = Evidence(
        code_attempt_id=ATTEMPT,
        bundle_sha256=BUNDLE,
        gates=[gate(g) for g in (*REQUIRED_GATES, GateName.BANDIT)],
        sandbox=SandboxEvidence("run-1", ATTEMPT, SandboxMode.CUBESANDBOX, RUNTIME, DATASET),
        test=TestEvidence(
            "test-1",
            "run-1",
            ATTEMPT,
            TestRunState.PASS,
            3,
            3,
            0,
            SUITE,
            frozenset({CaseKind.PASS_FIRST, CaseKind.NO_OP, CaseKind.ESCALATION}),
        ),
        current_dataset_sha256=DATASET,
        current_suite_sha256=SUITE,
        current_runtime_digest=RUNTIME,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def test_e14_1_t01_full_evidence_certifies() -> None:
    verdict = evaluate(evidence())
    assert verdict.certified and verdict.reasons == ()


def test_e14_1_t02_a_missing_gate_blocks_naming_it() -> None:
    gates = [g for g in evidence().gates if g.gate is not GateName.MYPY]
    verdict = evaluate(evidence(gates=gates))
    assert not verdict.certified
    assert verdict.reasons == ("MISSING_EVIDENCE:MYPY",)


def test_e14_1_every_required_gate_is_checked() -> None:
    for name in REQUIRED_GATES:
        gates = [g for g in evidence().gates if g.gate is not name]
        assert f"MISSING_EVIDENCE:{name}" in evaluate(evidence(gates=gates)).reasons


def test_e14_1_a_failed_gate_blocks() -> None:
    gates = [
        gate(g, status=GateStatus.FAILED) if g is GateName.RUFF else gate(g) for g in REQUIRED_GATES
    ]
    gates.append(gate(GateName.BANDIT))
    assert evaluate(evidence(gates=gates)).reasons == ("GATE_FAILED:RUFF",)


def test_e14_1_t03_a_local_docker_run_cannot_certify() -> None:
    sandbox = SandboxEvidence("run-1", ATTEMPT, SandboxMode.LOCAL_DOCKER_NONCERT, RUNTIME, DATASET)
    verdict = evaluate(evidence(sandbox=sandbox))
    assert verdict.reasons == ("NON_CERTIFYING_RUN",)


def test_e14_1_t04_fewer_than_three_cases_is_rejected() -> None:
    test = replace(evidence().test, total=2, passed=2)  # type: ignore[type-var]
    assert "MIN_SCENARIOS" in evaluate(evidence(test=test)).reasons


def test_e14_1_t05_a_missing_escalation_scenario_is_rejected() -> None:
    kinds = frozenset({CaseKind.PASS_FIRST, CaseKind.NO_OP, CaseKind.EDGE})
    test = replace(evidence().test, passed_kinds=kinds)  # type: ignore[type-var]
    assert evaluate(evidence(test=test)).reasons == ("REQUIRED_SCENARIO:ESCALATION",)


def test_e14_1_t06_evidence_on_different_bundles_is_rejected() -> None:
    gates = [
        gate(g, bundle_sha256="c" * 64) if g is GateName.MYPY else gate(g) for g in REQUIRED_GATES
    ]
    gates.append(gate(GateName.BANDIT))
    assert evaluate(evidence(gates=gates)).reasons == ("EVIDENCE_HASH_MISMATCH",)


def test_e14_1_gates_on_attempt_one_do_not_cover_tests_on_attempt_two() -> None:
    test = replace(evidence().test, code_attempt_id="attempt-2")  # type: ignore[type-var]
    assert evaluate(evidence(test=test)).reasons == ("EVIDENCE_HASH_MISMATCH",)


def test_e14_1_a_dataset_edited_after_the_run_blocks() -> None:
    assert "DATASET_CHANGED" in evaluate(evidence(current_dataset_sha256="e" * 64)).reasons


def test_e14_1_a_changed_suite_or_runtime_digest_blocks() -> None:
    assert "SUITE_CHANGED" in evaluate(evidence(current_suite_sha256="6" * 64)).reasons
    assert (
        "RUNTIME_CHANGED" in evaluate(evidence(current_runtime_digest="sha256:" + "q" * 64)).reasons
    )


def test_e14_1_bandit_high_blocks_but_medium_only_certifies() -> None:
    base = [gate(g) for g in REQUIRED_GATES]
    high = gate(GateName.BANDIT, severities=[Severity.MEDIUM, Severity.HIGH])
    medium = gate(GateName.BANDIT, severities=[Severity.MEDIUM, Severity.LOW])
    assert evaluate(evidence(gates=[*base, high])).reasons == ("BANDIT_HIGH",)
    assert evaluate(evidence(gates=[*base, medium])).certified


def test_e14_1_a_failing_test_run_blocks() -> None:
    test = replace(evidence().test, state=TestRunState.FAIL, passed=2, failed=1)  # type: ignore[type-var]
    assert "TESTS_FAILED" in evaluate(evidence(test=test)).reasons
