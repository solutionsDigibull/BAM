"""Tenant- and project-scoped `sandbox_runs` access (E13.2).

`claim` is how an activity attempt gets its row. Temporal delivers an activity at least once, so a
retry, a replay after a lost result and a stalled worker that wakes up late all have to end with
exactly one completed row per outcome:

* the same activity already FINISHED with a test outcome (anything but INFRA_ERROR): that is the
  answer, handed back as a replay and never run again;
* a STARTED row (a crashed or stalled attempt) is closed as INFRA_ERROR / SUPERSEDED, so there is
  never more than one live run per (code attempt, dataset) and the loser's late `finish` meets the
  frozen row (423) instead of overwriting;
* otherwise a new row with the next `run_no`.

Claimers for one (code attempt, dataset) are serialised by a transaction-scoped advisory lock on
Postgres: without it, several claimers read the same STARTED row and all try to close it, and every
one but the first fails on the row version. Call `claim` in its own short transaction and commit
right after, so the lock is held for milliseconds. The unique constraint stays as the backstop.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sbam_contracts.sandbox import (
    FailureCategory,
    InfraReason,
    SandboxMode,
    SandboxRunStatus,
    normalize_digest,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.sandbox.models import SandboxRunRecord

CLAIM_ATTEMPTS = 5


@dataclass(frozen=True)
class RunOutcome:
    """What a run ends with. `infra_reason` goes with INFRA_ERROR and with nothing else."""

    failure_category: FailureCategory | None = None
    infra_reason: InfraReason | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    vm_id: str | None = None
    egress_log_key: str | None = None
    outputs_key: str | None = None
    logs_key: str | None = None
    truncated: bool = False
    summary: dict[str, Any] | None = field(default=None, hash=False)


@dataclass(frozen=True)
class Claim:
    row: SandboxRunRecord
    replay: bool  # True: this activity already finished with a test outcome; do not run again


class SandboxRunRepository(TenantScopedRepository[SandboxRunRecord]):
    model = SandboxRunRecord

    def of_attempt(
        self, code_attempt_id: uuid.UUID, dataset_id: uuid.UUID | None = None
    ) -> list[SandboxRunRecord]:
        query = self._scoped().where(SandboxRunRecord.code_attempt_id == code_attempt_id)
        if dataset_id is not None:
            query = query.where(SandboxRunRecord.dataset_id == dataset_id)
        return list(self.session.scalars(query.order_by(SandboxRunRecord.run_no)))

    def claim(
        self,
        *,
        code_attempt_id: uuid.UUID,
        dataset_id: uuid.UUID,
        mode: SandboxMode,
        runtime_digest: str,
        workflow_id: str,
        activity_id: str,
        activity_attempt: int,
        now: datetime,
    ) -> Claim:
        digest = normalize_digest(runtime_digest)
        self._lock(code_attempt_id, dataset_id)
        for _ in range(CLAIM_ATTEMPTS):
            rows = self.of_attempt(code_attempt_id, dataset_id)
            for row in rows:
                if (
                    row.status == SandboxRunStatus.FINISHED
                    and row.temporal_workflow_id == workflow_id
                    and row.activity_id == activity_id
                    and row.failure_category != FailureCategory.INFRA_ERROR
                ):
                    return Claim(row, replay=True)
            for row in rows:
                if row.status == SandboxRunStatus.STARTED:
                    self.finish(
                        row,
                        RunOutcome(
                            failure_category=FailureCategory.INFRA_ERROR,
                            infra_reason=InfraReason.SUPERSEDED,
                            vm_id=row.vm_id,
                        ),
                        now,
                    )
            run_no = max((r.run_no for r in rows), default=0) + 1
            entity = SandboxRunRecord(
                code_attempt_id=code_attempt_id,
                dataset_id=dataset_id,
                run_no=run_no,
                mode=mode.value,
                status=SandboxRunStatus.STARTED.value,
                runtime_digest=digest,
                truncated=False,
                is_frozen=False,
                temporal_workflow_id=workflow_id,
                activity_id=activity_id,
                activity_attempt=activity_attempt,
                started_at=now,
            )
            try:
                with self.session.begin_nested():
                    self.add(entity)
            except IntegrityError:
                continue  # another claimer took this run_no: read the rows again
            return Claim(entity, replay=False)
        raise RuntimeError("could not claim a sandbox run: the number kept being taken")

    def _lock(self, code_attempt_id: uuid.UUID, dataset_id: uuid.UUID) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return  # SQLite (unit tests) has one writer at a time
        key = f"sandbox_run:{self.ctx.tenant_id}:{code_attempt_id}:{dataset_id}"
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    def record_vm(self, row: SandboxRunRecord, vm_id: str) -> None:
        """The VM exists: write its id before anything else can fail, so the reaper can find it."""
        row.vm_id = vm_id
        self._flush()

    def finish(self, row: SandboxRunRecord, outcome: RunOutcome, now: datetime) -> SandboxRunRecord:
        """STARTED -> FINISHED and frozen. A row that is already frozen raises `RowFrozen` (423)."""
        self._check_owned(row)
        started = row.started_at
        row.status = SandboxRunStatus.FINISHED.value
        row.is_frozen = True
        row.finished_at = now
        row.failure_category = (
            outcome.failure_category.value if outcome.failure_category is not None else None
        )
        row.infra_reason = outcome.infra_reason.value if outcome.infra_reason is not None else None
        row.exit_code = outcome.exit_code
        row.vm_id = outcome.vm_id or row.vm_id
        row.egress_log_key = outcome.egress_log_key
        row.outputs_key = outcome.outputs_key
        row.logs_key = outcome.logs_key
        row.truncated = outcome.truncated
        row.summary = outcome.summary
        row.duration_ms = (
            outcome.duration_ms
            if outcome.duration_ms is not None
            else max(0, int((now - started).total_seconds() * 1000))
        )
        self._flush()
        return row


__all__ = ["CLAIM_ATTEMPTS", "Claim", "RunOutcome", "SandboxRunRepository"]
