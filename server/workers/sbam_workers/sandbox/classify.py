"""One place that turns what a run produced into an outcome (E13.2, ADR 0023, decision 9).

A wrong answer is expensive both ways. Calling a dead host a test failure burns the two corrections
a Sub-Skill gets; calling a real bug an infrastructure error retries it forever and never reaches
`correct`. So every rule below is evidence-based and tested in both directions.

Rules worth knowing
  * Runner exit codes are a protocol: 0 outputs written (cases may still have failed), 71 the
    ephemeral database could not be set up, 72 the dataset's seed SQL failed, anything else is the
    runner itself failing.
  * A runner that overran the whole deadline is the *code's* fault (TIMEOUT) only if the VM still
    answers a liveness probe afterwards; a VM that does not answer is infrastructure.
  * Each case's verdict is recomputed here from its trace. The runner's own `passed` flag is checked
    against it but never trusted, and the case ids must be exactly the ones that were sent.
  * Text that came out of the code under test goes into findings, and findings go into a prompt for
    the correction stage. It is cut, stripped to printable ASCII and stripped of anything that looks
    like a prompt delimiter before it leaves this module.
"""

import re
from dataclasses import dataclass, field
from typing import Final

from pydantic import ValidationError
from sbam_contracts.build_dashboard import Finding, GateName
from sbam_contracts.sandbox import (
    EgressEvent,
    EgressSource,
    EgressVerdict,
    FailureCategory,
    InfraReason,
    RunnerCaseResult,
    RunnerOutputs,
    SandboxMode,
    normalize_digest,
)

from sbam_workers.sandbox.backend import ExecResult
from sbam_workers.test.harness import HarnessTrace

EXIT_OK: Final = 0
EXIT_DATABASE: Final = 71
EXIT_SEED: Final = 72
MESSAGE_LIMIT: Final = 300

# most specific first: this is the order a run-level category is chosen in
PRIORITY: Final = (
    FailureCategory.EGRESS_BLOCKED,
    FailureCategory.IMPORT_ERROR,
    FailureCategory.OOM,
    FailureCategory.TIMEOUT,
    FailureCategory.RUNTIME_ERROR,
    FailureCategory.OUTPUT_SCHEMA_MISMATCH,
    FailureCategory.ASSERTION_FAILED,
)
_DELIMITERS: Final = re.compile(
    r"</?\s*(?:data|image|system|assistant|user|instructions?)\b[^>]*>?", re.I
)
_SPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True)
class CaseVerdict:
    case_id: str
    passed: bool
    category: FailureCategory | None
    message: str | None = None
    pass_no: int | None = None
    step: str | None = None


@dataclass(frozen=True)
class Classified:
    category: FailureCategory | None  # None: every case passed
    infra_reason: InfraReason | None
    cases: tuple[CaseVerdict, ...] = ()
    blocked_egress: tuple[EgressEvent, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.category is None

    def findings(self) -> list[Finding]:
        out = []
        for case in self.cases:
            if case.passed or case.category is None:
                continue
            where = f" (pass {case.pass_no}, {case.step})" if case.pass_no and case.step else ""
            detail = f": {case.message}" if case.message else ""
            out.append(
                Finding(
                    gate=GateName.LOOP_TESTS,
                    code=case.category.value,
                    message=f"case {sanitize(case.case_id, 80)} failed{where}{detail}",
                )
            )
        for event in self.blocked_egress[:5]:
            target = f"{sanitize(event.dst_host, 120)}:{event.dst_port}"
            out.append(
                Finding(
                    gate=GateName.LOOP_TESTS,
                    code=FailureCategory.EGRESS_BLOCKED.value,
                    message=f"a connection to {target} was blocked",
                )
            )
        return out


def sanitize(text: str | None, limit: int = MESSAGE_LIMIT) -> str:
    """Printable ASCII, one line, no prompt delimiters, at most `limit` characters."""
    if not text:
        return ""
    cleaned = _DELIMITERS.sub(" ", text)
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in cleaned)
    cleaned = _SPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.replace("<", " ").replace(">", " ")
    cleaned = _SPACE.sub(" ", cleaned).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3].rstrip() + "..."


def infra(reason: InfraReason, *notes: str) -> Classified:
    return Classified(FailureCategory.INFRA_ERROR, reason, notes=notes)


def blocked(events: list[EgressEvent]) -> tuple[EgressEvent, ...]:
    """BLOCK verdicts from a source that saw the connection; a guest's own report counts too."""
    return tuple(
        e
        for e in events
        if e.verdict is EgressVerdict.BLOCK
        and e.source in (EgressSource.L7_PROXY, EgressSource.HOST_TAP, EgressSource.GUEST_PROBE)
    )


def _verdict(raw_case: RunnerCaseResult, expected_terminal: str) -> CaseVerdict:
    """One case from its trace, recomputed. Raises ValidationError for a trace that is not one."""
    trace = HarnessTrace.model_validate(raw_case.trace)
    if trace.case_id != raw_case.case_id:
        raise ValueError("trace case id differs from the result's")
    if trace.error is not None:
        category = FailureCategory(trace.error.category)
        return CaseVerdict(
            raw_case.case_id,
            False,
            category,
            sanitize(trace.error.message),
            trace.error.pass_no,
            trace.error.step,
        )
    if trace.terminal != expected_terminal:
        return CaseVerdict(
            raw_case.case_id,
            False,
            FailureCategory.ASSERTION_FAILED,
            sanitize(f"expected {expected_terminal}, got {trace.terminal}"),
        )
    return CaseVerdict(raw_case.case_id, True, None)


def classify(
    *,
    mode: SandboxMode,
    expected_digest: str,
    expected_case_ids: list[str],
    exec_result: ExecResult | None,
    outputs: RunnerOutputs | None,
    vm_alive: bool,
    egress: list[EgressEvent],
) -> Classified:
    seen_blocks = blocked(egress)
    if outputs is None:
        return _without_outputs(exec_result, vm_alive, seen_blocks)
    if outputs.mode is not mode:
        return infra(InfraReason.RUNNER_FAILED, "the runner reported a different mode")
    try:
        if normalize_digest(outputs.runtime_digest) != normalize_digest(expected_digest):
            return infra(InfraReason.TEMPLATE_DIGEST_MISMATCH, "the runner saw another runtime")
    except ValueError:
        return infra(InfraReason.RUNNER_FAILED, "the runner reported no usable digest")
    reported = [c.case_id for c in outputs.cases]
    if sorted(reported) != sorted(expected_case_ids):
        return infra(
            InfraReason.RUNNER_FAILED, "the runner's cases are not the cases that were sent"
        )
    verdicts: list[CaseVerdict] = []
    for case in outputs.cases:
        try:
            verdict = _verdict(case, case.expected_terminal)
        except (ValidationError, ValueError):
            return infra(InfraReason.RUNNER_FAILED, "a case trace is not a valid harness trace")
        if verdict.passed != case.passed:
            return infra(InfraReason.RUNNER_FAILED, "a case's passed flag contradicts its trace")
        verdicts.append(verdict)
    failures = [v for v in verdicts if not v.passed]
    if not failures:
        return Classified(None, None, tuple(verdicts), seen_blocks)
    categories = {v.category for v in failures}
    category = (
        FailureCategory.EGRESS_BLOCKED
        if seen_blocks
        else next(c for c in PRIORITY if c in categories)
    )
    return Classified(category, None, tuple(verdicts), seen_blocks)


def _without_outputs(
    result: ExecResult | None, vm_alive: bool, seen_blocks: tuple[EgressEvent, ...]
) -> Classified:
    if result is None:
        return infra(InfraReason.RUNNER_FAILED, "the runner never started")
    if result.exit_code == EXIT_DATABASE and not result.timed_out:
        return infra(InfraReason.RUNNER_FAILED, "the ephemeral database could not be set up")
    if result.exit_code == EXIT_SEED and not result.timed_out:
        return infra(InfraReason.DATASET_INVALID, "the dataset's seed SQL failed")
    if not vm_alive:
        return infra(InfraReason.HOST_UNREACHABLE, "the VM stopped answering")
    if result.timed_out:
        category = FailureCategory.EGRESS_BLOCKED if seen_blocks else FailureCategory.TIMEOUT
        return Classified(category, None, blocked_egress=seen_blocks, notes=("the run overran",))
    return infra(InfraReason.RUNNER_FAILED, f"the runner exited {result.exit_code} with no outputs")
