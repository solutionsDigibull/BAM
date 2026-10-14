"""Ingestion stage (owner: E4.x).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import IdsResult, IngestInput
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for


@activity_for(TaskQueue.INGEST)
@activity.defn(name="ingest_documents")
async def ingest_documents(inp: IngestInput) -> IdsResult:
    """Real: one DocumentIngestion child per file (scan, detect, parse, OCR)."""
    return IdsResult(ids=list(inp.document_version_ids))
