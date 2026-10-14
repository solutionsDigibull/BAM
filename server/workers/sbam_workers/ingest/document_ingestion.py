"""`DocumentIngestion` workflow (TD §13.2): STUB, started by the upload post-finish hook (E4.2).

One execution per uploaded file, id `ingest-<document_version_id>` on sbam-ingest. It does nothing
yet. #62 adds the scan, then detect / parse / OCR follow (E4.3 to E4.7); each keeps this name and
the input and output types.
"""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import DOCUMENT_INGESTION
    from sbam_contracts.pipeline import IdsResult, StageRef
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.registry import workflow_for


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=DOCUMENT_INGESTION)
class DocumentIngestion:
    @workflow.run
    async def run(self, inp: StageRef) -> IdsResult:
        return IdsResult(ids=[inp.ref_id])
