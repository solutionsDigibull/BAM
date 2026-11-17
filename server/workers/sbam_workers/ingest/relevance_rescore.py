"""`RelevanceRescore` workflow (#802): score every parsed document of a project against its
current goal, e.g. after the FDE set or changed it (`POST P/documents/relevance/rescore`).

One execution per project, id `relevance-<project_id>`; a newer start replaces a running one, so the
latest goal wins. `parsed_documents` lists the project's PARSED, CLEAN versions; each is scored with
`score_relevance` (a version already scored against this goal is skipped inside the activity). A
document that cannot be scored is left as it is and the others go on: relevance is advice only.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import RELEVANCE_RESCORE
    from sbam_contracts.pipeline import IdsResult, StageRef
    from sbam_contracts.runs import RunInput, TaskQueue

    from sbam_workers.ingest import relevance
    from sbam_workers.registry import workflow_for


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=RELEVANCE_RESCORE)
class RelevanceRescore:
    @workflow.run
    async def run(self, inp: RunInput) -> IdsResult:
        found = await workflow.execute_activity(
            relevance.parsed_documents,
            inp,
            task_queue=TaskQueue.INGEST.value,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        scored = []
        for version_id in found.ids:
            ref = StageRef(
                tenant_id=inp.tenant_id,
                project_id=inp.project_id,
                run_id=inp.run_id,
                correlation_id=inp.correlation_id,
                ref_id=version_id,
            )
            try:
                result = await workflow.execute_activity(
                    relevance.score_relevance,
                    ref,
                    task_queue=TaskQueue.INGEST.value,
                    start_to_close_timeout=relevance.SCORE_TIMEOUT,
                    retry_policy=relevance.SCORE_RETRY,
                )
            except ActivityError:
                continue  # this document stays unscored; the others go on
            scored.extend(result.ids)
        return IdsResult(ids=scored)
