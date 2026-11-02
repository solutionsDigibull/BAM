"""The judging step of `run_sandbox_tests` (E13.6, ADR 0023): the suite's verdict on a finished run.

The classifier (`classify.py`) decides from the runner's own traces; the suite's assertions are
stricter (the output schema, `state_handoff_id`, the handoff rows, the budget, the UI updates), so a
run the classifier passed can still fail here. `StageJudge.judge` runs the suite over the runner's
`outputs.json`, writes the `test_runs` row (one per sandbox run, idempotent) and returns the
`Classified` the stage should finish the run with:

  * the suite agrees, or the run already failed: the classification is returned as it is;
  * the suite fails a run the classifier passed: `ASSERTION_FAILED`, one verdict per failed case
    carrying the assertion messages (field names and counts, never output values), so the corrector
    reads them through the same LOOP_TESTS findings;
  * a case has an `llm_rubric` and no grader is configured: `JudgeBlocked`, and no row is written,
    so nothing is scored as the code's fault because the platform could not grade it.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_contracts.build import BuildStageInput
from sbam_contracts.harness import HarnessTrace
from sbam_contracts.promptfoo import AssertionResult, Suite
from sbam_contracts.sandbox import FailureCategory, InfraReason, RunnerOutputs
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.sandbox.classify import CaseVerdict, Classified, sanitize
from sbam_workers.sandbox.load import Identity
from sbam_workers.test.judge import (
    Grader,
    Judgement,
    TestingBlocked,
    judge_run,
    judge_with_grader,
    needs_rubric,
    record_run,
    row_values,
)
from sbam_workers.test.promptfoo_suite import load_suite

IsatvonOf = Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]]


class JudgeBlocked(Exception):  # noqa: N818 - it blocks testing, it is not an error of the code
    """The suite could not be judged for a reason that is not the code's: retry, then block."""

    def __init__(self, reason: InfraReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def failed_cases(judgement: Judgement) -> tuple[CaseVerdict, ...]:
    """One failed verdict per case that has a failed assertion, with the assertions' messages."""
    by_case: dict[str, list[AssertionResult]] = {}
    for result in judgement.results:
        if not result.passed:
            by_case.setdefault(result.case, []).append(result)
    return tuple(
        CaseVerdict(
            case,
            False,
            FailureCategory.ASSERTION_FAILED,
            sanitize("; ".join(f"{r.type.value}: {r.message}" for r in results)),
        )
        for case, results in by_case.items()
    )


class StageJudge:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        isatvon_of: IsatvonOf,
        grader: Grader | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self.sessions = sessions
        self.isatvon_of = isatvon_of
        self.grader = grader
        self.storage = storage

    def _store_traces(self, inp: BuildStageInput, outputs: RunnerOutputs) -> str | None:
        """The run's per-case harness traces, stored with the run for the test panel (E13.7). The
        key goes in `report_key`. Without a storage backend (unit tests) nothing is stored."""
        if self.storage is None:
            return None
        traces = [HarnessTrace.model_validate(case.trace) for case in outputs.cases]
        document = {"traces": [t.model_dump(mode="json", by_alias=True) for t in traces]}
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        stored = self.storage.put(
            json.dumps(document, sort_keys=True).encode(), scope, Kind.TESTS, "application/json"
        )
        return stored.storage_key

    async def _suite(self, inp: BuildStageInput) -> Suite:
        isatvon = await self.isatvon_of(inp)
        suite, _ = await asyncio.to_thread(load_suite, self.sessions, inp, isatvon)
        return suite

    async def _judgement(self, suite: Suite, outputs: RunnerOutputs) -> Judgement:
        if not needs_rubric(suite):
            return judge_run(suite, outputs)
        if self.grader is None:
            raise JudgeBlocked(InfraReason.NOT_CONFIGURED, "a case needs a rubric grader")
        try:
            return await judge_with_grader(suite, outputs, self.grader)
        except TestingBlocked:
            raise JudgeBlocked(InfraReason.RUNNER_FAILED, "the rubric grader is down") from None

    async def judge(
        self,
        inp: BuildStageInput,
        identity: Identity,
        run_id: Any,
        outputs: RunnerOutputs,
        classified: Classified,
    ) -> Classified:
        suite = await self._suite(inp)
        judgement = await self._judgement(suite, outputs)
        values = row_values(
            sub_skill_id=inp.sub_skill_id,
            code_attempt_id=identity.attempt_id,
            sandbox_run_id=run_id,
            suite=suite,
            judgement=judgement,
            report_key=await asyncio.to_thread(self._store_traces, inp, outputs),
        )
        await record_run(self.sessions, TenantContext(inp.tenant_id, inp.project_id), values)
        if not classified.passed or judgement.green:
            return classified
        return Classified(
            FailureCategory.ASSERTION_FAILED,
            None,
            failed_cases(judgement),
            classified.blocked_egress,
            classified.notes,
        )
