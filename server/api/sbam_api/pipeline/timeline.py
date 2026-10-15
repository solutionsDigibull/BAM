"""The dashboard's pipeline panel (E4.1, TD §8.4): step timeline and document counts.

Steps are the pipeline states in order. Their status comes from the project's current state; their
`started_at` / `ended_at` come from the project_pipeline audit trail (one row per transition), read
only for the latest run: the rows since the last entry into INGESTING (a restart or scope change
starts a new run, and the earlier attempt is superseded). A project whose state was changed without
audit rows (legacy data) still gets statuses, just without timestamps.
"""

from collections.abc import Sequence
from datetime import datetime

from sbam_contracts.projects import PipelineCounts, PipelineStep, PipelineStepStatus
from sbam_contracts.state_machines import DocumentVersionState as D
from sbam_contracts.state_machines import ProjectPipelineState as P

from sbam_api.audit.models import AuditLog

STEPS = (
    P.INGESTING,
    P.KNOWLEDGE_BUILDING,
    P.SOW_GENERATING,
    P.SOW_REVIEW,
    P.SOW_APPROVED,
    P.WORKFLOW_CREATED,
)
FAILED_AT = {
    P.INGESTION_FAILED: P.INGESTING,
    P.KNOWLEDGE_FAILED: P.KNOWLEDGE_BUILDING,
    P.SOW_GENERATION_FAILED: P.SOW_GENERATING,
}
S = PipelineStepStatus


def _entered(row: AuditLog) -> str | None:
    state = (row.after or {}).get("state")
    return state if isinstance(state, str) else None


def _latest_run(history: Sequence[AuditLog]) -> list[AuditLog]:
    starts = [i for i, row in enumerate(history) if _entered(row) == P.INGESTING.value]
    return list(history[starts[-1] :]) if starts else []


def _statuses(state: P, last_reached: P | None) -> dict[P, PipelineStepStatus]:
    if state is P.IDLE:
        return dict.fromkeys(STEPS, S.PENDING)
    if state is P.CANCELLED:  # stopped wherever it was: the last step it reached is cancelled
        reached = STEPS.index(last_reached) if last_reached in STEPS else 0
        return {
            step: S.DONE if i < reached else S.CANCELLED if i == reached else S.PENDING
            for i, step in enumerate(STEPS)
        }
    current = FAILED_AT.get(state, state)
    at = STEPS.index(current)
    here = (
        S.FAILED
        if state in FAILED_AT
        else (
            S.DONE
            if state is P.WORKFLOW_CREATED  # the last step: nothing runs after it
            else S.RUNNING
        )
    )
    return {
        step: S.DONE if i < at else here if i == at else S.PENDING for i, step in enumerate(STEPS)
    }


def build_steps(state: P, history: Sequence[AuditLog]) -> list[PipelineStep]:
    run = _latest_run(history)
    last_reached = next(
        (P(s) for row in reversed(run) if (s := _entered(row)) in {x.value for x in STEPS}), None
    )
    statuses = _statuses(state, last_reached)
    steps = []
    for step in STEPS:
        started: datetime | None = None
        ended: datetime | None = None
        if statuses[step] is not S.PENDING:
            entries = [i for i, row in enumerate(run) if _entered(row) == step.value]
            if entries:
                started = run[entries[-1]].at
                if entries[-1] + 1 < len(run):
                    ended = run[entries[-1] + 1].at
                elif statuses[step] is S.DONE:
                    ended = started  # terminal step: reached and finished at the same moment
        steps.append(
            PipelineStep(name=step, status=statuses[step], started_at=started, ended_at=ended)
        )
    return steps


def build_counts(by_state: dict[str, int]) -> PipelineCounts:
    def n(*states: D) -> int:
        return sum(by_state.get(s.value, 0) for s in states)

    return PipelineCounts(
        uploaded=n(D.UPLOADED, D.SCANNING, D.PARSING),
        parsed=n(D.PARSED),
        rejected=n(D.REJECTED),
        failed=n(D.SCAN_FAILED, D.PARSE_FAILED),
    )
