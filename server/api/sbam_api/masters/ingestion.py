"""Hand master documents to Temporal for parsing (E3.8b).

`MasterDocumentIngestion` is started with the id `master-doc-<master_document_id>`, so starting
twice finds the same execution. The attach API starts it for the document it just scanned CLEAN,
and for any other CLEAN document of the tenant still PENDING (Temporal was down when it was
attached). If Temporal is down the document stays PENDING and the attach still succeeds: the next
attach starts it.
"""

import logging
import uuid

from sbam_contracts.documents import MASTER_DOCUMENT_INGESTION
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import TaskQueue, master_document_workflow_id
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from sbam_api.core.ids import uuid7
from sbam_api.masters.models import MasterDocument
from sbam_api.temporal.client import RPC_TIMEOUT

log = logging.getLogger("sbam_api.masters")
TENANT_SCOPE_PROJECT = uuid.UUID(int=0)  # a tenant-wide document has no project


async def start_parse(client: Client, document: MasterDocument, correlation_id: str) -> bool:
    """True when the workflow is running for this document. False = Temporal is unavailable."""
    ref = StageRef(
        tenant_id=document.tenant_id,
        project_id=document.project_id or TENANT_SCOPE_PROJECT,
        run_id=uuid7(),
        correlation_id=correlation_id,
        ref_id=document.id,
    )
    try:
        await client.start_workflow(
            MASTER_DOCUMENT_INGESTION,
            ref,
            id=master_document_workflow_id(document.id),
            task_queue=TaskQueue.INGEST.value,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            rpc_timeout=RPC_TIMEOUT,
        )
    except (RPCError, RuntimeError, WorkflowFailureError):
        log.warning("master document parse not started yet")
        return False
    return True


async def start_all(client: Client, documents: list[MasterDocument], correlation_id: str) -> int:
    """Start each document's parse; stops at the first one Temporal refuses."""
    started = 0
    for document in documents:
        if not await start_parse(client, document, correlation_id):
            break
        started += 1
    return started
