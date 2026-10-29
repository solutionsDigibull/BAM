"""`PurgeDeleted` (E4.9): the daily job that removes documents deleted more than 90 days ago.

A Temporal Schedule starts it at 02:00 UTC (`purge_schedule.ensure_purge_schedule`, run when an
ingest worker starts). The workflow is deterministic: it reads the time from `workflow.now()` and
passes it to the activities, which do all the work (`purge_activities`). It asks for the due
documents and purges each in its own activity (a failure is counted, not fatal, and that document is
not asked for again in this run), and repeats until none are left. A run that left documents behind
fails, so it is visible; the next day's run tries them again.

Before a document's files go, its Cognee items are removed on the knowledge queue (#449,
`remove_document_knowledge`): only worker-knowledge has Cognee. If that fails after its retries,
the document is not purged; it stays DELETED and the next run tries again. Runs started before
this change replay unchanged (`workflow.patched`).
"""

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import (
        PURGE_DELETED,
        PurgeOutcome,
        PurgeRequest,
        PurgeResult,
        PurgeTarget,
    )
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.ingest.purge_activities import list_purgeable_documents, purge_document
    from sbam_workers.knowledge.purge import remove_document_knowledge
    from sbam_workers.registry import workflow_for

REMOVE_KNOWLEDGE = "purge-removes-knowledge-449"  # workflow.patched id
MAX_ROUNDS = 20  # x LIST_LIMIT documents per run; anything left is picked up tomorrow
PARALLEL = 10
PURGE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=PURGE_DELETED)
class PurgeDeleted:
    @workflow.run
    async def run(self) -> PurgeResult:
        as_of = workflow.now()
        result = PurgeResult()
        failed_ids: list[str] = []
        for _ in range(MAX_ROUNDS):
            targets: list[PurgeTarget] = await workflow.execute_activity(
                list_purgeable_documents,
                args=[as_of, failed_ids],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=PURGE_RETRY,
            )
            if not targets:
                break
            for start in range(0, len(targets), PARALLEL):
                batch = targets[start : start + PARALLEL]
                outcomes = await asyncio.gather(
                    *(self._purge(t, as_of) for t in batch), return_exceptions=True
                )
                for target, outcome in zip(batch, outcomes, strict=True):
                    if outcome == PurgeOutcome.PURGED:
                        result.purged += 1
                    elif outcome == PurgeOutcome.SKIPPED:
                        result.skipped += 1
                    else:
                        result.failed += 1
                        failed_ids.append(str(target.document_id))
        if result.failed:
            raise ApplicationError(
                f"{result.failed} document(s) not purged; the next run retries them",
                type="PurgeIncomplete",
                non_retryable=True,
            )
        return result

    async def _purge(self, target: PurgeTarget, as_of: datetime) -> PurgeOutcome:
        request = PurgeRequest(target=target, as_of=as_of)
        if workflow.patched(REMOVE_KNOWLEDGE):
            await workflow.execute_activity(
                remove_document_knowledge,
                request,
                task_queue=TaskQueue.KNOWLEDGE.value,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=PURGE_RETRY,
            )
        outcome: PurgeOutcome = await workflow.execute_activity(
            purge_document,
            request,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=PURGE_RETRY,
        )
        return outcome
