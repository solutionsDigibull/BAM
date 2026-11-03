"""The Sub-Skill certification checklist (E14.1): which evidence a build needs before it certifies.

`evaluate` is a pure function of the evidence the certify activity loaded, so every rule is a unit
test. Certification is automatic and all-or-nothing: the first rule that fails names a reason
(`MISSING_EVIDENCE:MYPY`, `EVIDENCE_HASH_MISMATCH`, ...) and nothing is written. Evidence is only
good when every item is about one code bundle: gates, sandbox run and test run must name the same
`code_attempt_id` and the gates the same `bundle_sha256`.

Reasons name gates, counts and ids, never code or output.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.promptfoo import TestRunState
from sbam_contracts.sandbox import SandboxMode
from sbam_contracts.testgen import MIN_CASES, REQUIRED_KINDS, CaseKind

# Every gate that must have passed on the bundle. BANDIT is not here: it passes unless a HIGH
# finding is on record (MEDIUM and LOW are reported, never blocking). CODEGEN and LOOP_TESTS are
# the dashboard's columns for generation and the sandbox loop, which have their own evidence.
REQUIRED_GATES: Final = tuple(
    g for g in GateName if g not in {GateName.CODEGEN, GateName.BANDIT, GateName.LOOP_TESTS}
)


class Reason(StrEnum):
    NON_CERTIFYING_RUN = "NON_CERTIFYING_RUN"
    MIN_SCENARIOS = "MIN_SCENARIOS"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    TESTS_FAILED = "TESTS_FAILED"
    GATE_FAILED = "GATE_FAILED"
    BANDIT_HIGH = "BANDIT_HIGH"
    DATASET_CHANGED = "DATASET_CHANGED"
    SUITE_CHANGED = "SUITE_CHANGED"
    RUNTIME_CHANGED = "RUNTIME_CHANGED"


@dataclass(frozen=True)
class GateEvidence:
    gate: GateName
    status: GateStatus
    code_attempt_id: str | None
    bundle_sha256: str | None
    severities: Sequence[Severity | None] = ()  # one per finding


@dataclass(frozen=True)
class SandboxEvidence:
    id: str
    code_attempt_id: str
    mode: SandboxMode
    runtime_digest: str
    dataset_sha256: str


@dataclass(frozen=True)
class TestEvidence:
    __test__ = False

    id: str
    sandbox_run_id: str
    code_attempt_id: str
    state: TestRunState
    total: int
    passed: int
    failed: int
    suite_sha256: str
    passed_kinds: frozenset[CaseKind]  # the kinds of the cases that passed


@dataclass(frozen=True)
class Evidence:
    """What the activity loaded for one candidate build and the facts it must still match."""

    code_attempt_id: str
    bundle_sha256: str
    gates: Sequence[GateEvidence]
    sandbox: SandboxEvidence | None
    test: TestEvidence | None
    current_dataset_sha256: str  # the dataset as it is now (the FDE may have edited it)
    current_suite_sha256: str
    current_runtime_digest: str


@dataclass(frozen=True)
class Verdict:
    certified: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _missing(what: str) -> str:
    return f"MISSING_EVIDENCE:{what}"


def _gate_reasons(evidence: Evidence) -> list[str]:
    by_gate = {g.gate: g for g in evidence.gates}
    reasons: list[str] = []
    for gate in REQUIRED_GATES:
        row = by_gate.get(gate)
        if row is None:
            reasons.append(_missing(gate))
        elif row.status is not GateStatus.PASSED:
            reasons.append(f"{Reason.GATE_FAILED}:{gate}")
    bandit = by_gate.get(GateName.BANDIT)
    if bandit is None:
        reasons.append(_missing(GateName.BANDIT))
    elif Severity.HIGH in bandit.severities or bandit.status is GateStatus.FAILED:
        reasons.append(Reason.BANDIT_HIGH.value)
    return reasons


def _same_bundle(evidence: Evidence) -> bool:
    for g in evidence.gates:
        if g.code_attempt_id != evidence.code_attempt_id:
            return False
        if g.bundle_sha256 is not None and g.bundle_sha256 != evidence.bundle_sha256:
            return False
    sandbox, test = evidence.sandbox, evidence.test
    return (
        (sandbox is None or sandbox.code_attempt_id == evidence.code_attempt_id)
        and (test is None or test.code_attempt_id == evidence.code_attempt_id)
        and (sandbox is None or test is None or test.sandbox_run_id == sandbox.id)
    )


def _test_reasons(evidence: Evidence) -> list[str]:
    sandbox, test = evidence.sandbox, evidence.test
    if sandbox is None:
        return [_missing("SANDBOX_RUN")]
    if not sandbox.mode.certifying:
        return [Reason.NON_CERTIFYING_RUN.value]  # a local-docker run never certifies
    if test is None:
        return [_missing("TEST_RUN")]
    if test.state is TestRunState.NON_CERTIFYING:
        return [Reason.NON_CERTIFYING_RUN.value]
    reasons: list[str] = []
    if test.state is not TestRunState.PASS or test.failed:
        reasons.append(Reason.TESTS_FAILED.value)
    if test.total < MIN_CASES:
        reasons.append(Reason.MIN_SCENARIOS.value)
    for kind in sorted(REQUIRED_KINDS):
        if kind not in test.passed_kinds:
            reasons.append(f"REQUIRED_SCENARIO:{kind}")
    if sandbox.dataset_sha256 != evidence.current_dataset_sha256:
        reasons.append(Reason.DATASET_CHANGED.value)
    if test.suite_sha256 != evidence.current_suite_sha256:
        reasons.append(Reason.SUITE_CHANGED.value)
    if sandbox.runtime_digest != evidence.current_runtime_digest:
        reasons.append(Reason.RUNTIME_CHANGED.value)
    return reasons


def evaluate(evidence: Evidence) -> Verdict:
    """Certify only when every item is present, green and about the same bundle."""
    if not _same_bundle(evidence):
        return Verdict(False, (Reason.EVIDENCE_HASH_MISMATCH.value,))
    reasons = _gate_reasons(evidence) + _test_reasons(evidence)
    return Verdict(not reasons, tuple(reasons))
