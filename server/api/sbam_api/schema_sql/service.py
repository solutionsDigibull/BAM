"""`W/schema-sql` (E9.4, TD §8.8, §12.6): read a Workflow's schema.sql and set its uploaded base.

`PUT W/schema-sql/source` makes one of the project's documents (a parsed, clean `.sql`) the base
the next build starts from. The SQL is data: it is parsed with the SCHEMA_SQL allowlist and never
executed here; a statement it can't parse or doesn't allow is 422 SCHEMA_PARSE_ERROR naming the
statement, and nothing is stored. The base points at the document's own stored bytes (uploads are
content-addressed), so nothing is copied. A Workflow being built is 409 WORKFLOW_BUILDING, a frozen
one 423.
"""

import uuid

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.build_dashboard import Finding, GateStatus
from sbam_contracts.documents import ScanStatus
from sbam_contracts.schema_sql import (
    MAX_SOURCE_BYTES,
    SchemaSource,
    SchemaSqlSourcePut,
    WorkflowSchemaSql,
)
from sbam_contracts.state_machines import DocumentVersionState, WorkflowState
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.repository import DocumentRepository, DocumentVersionRepository
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.schema_sql.validate import check
from sbam_api.storage.base import StorageBackend
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

SQL_TYPE = "sql"  # Filetype #9's extension, as type detection records it


def _workflow(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Workflow:
    workflow: Workflow | None = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    return workflow


def _document_error(message: str, code: ErrorCode = ErrorCode.VALIDATION_ERROR) -> ApiError:
    return ApiError(
        422, code, "Not a usable schema", errors=[FieldError(path="document_id", message=message)]
    )


def body(row: WorkflowSchema, storage: StorageBackend) -> WorkflowSchemaSql:
    sql = ""
    if row.storage_key:
        with storage.open(row.storage_key) as stream:
            sql = stream.read().decode("utf-8", errors="replace")
    return WorkflowSchemaSql(
        workflow_id=row.workflow_id,
        version=row.version,
        source=SchemaSource(row.source),
        source_document_id=row.source_document_id,
        sha256=row.sha256,
        tables=list(row.tables),
        status=GateStatus(row.status),
        findings=[Finding.model_validate(f) for f in row.findings],
        sql=sql,
    )


def get_schema(
    session: Session,
    storage: StorageBackend,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    version: int | None,
) -> WorkflowSchemaSql:
    _workflow(session, ctx, workflow_id)
    row = WorkflowSchemaRepository(session, ctx).latest(workflow_id, version=version)
    if row is None:
        raise not_found()
    return body(row, storage)


def set_source(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    put: SchemaSqlSourcePut,
) -> WorkflowSchemaSql:
    ctx = user.context(project_id)
    state = WorkflowState(_workflow(session, ctx, workflow_id).state)
    if state is WorkflowState.FROZEN:
        raise ApiError(
            423, ErrorCode.WORKFLOW_FROZEN, "Workflow frozen", "Create a new version to change it."
        )
    if state is WorkflowState.BUILDING:
        raise ApiError(
            409, ErrorCode.WORKFLOW_BUILDING, "Workflow building", "Wait for the build to finish."
        )

    if DocumentRepository(session, ctx).get(put.document_id) is None:
        raise not_found()  # another project's (or tenant's) document is not found either
    version = DocumentVersionRepository(session, ctx).latest_for_document(put.document_id)
    if version is None or version.detected_type != SQL_TYPE:
        raise _document_error("The base schema must be a .sql file", ErrorCode.UNSUPPORTED_TYPE)
    if version.scan_status != ScanStatus.CLEAN.value:
        raise _document_error("The file has not passed the malware scan")
    if version.state != DocumentVersionState.PARSED.value:
        raise _document_error("The file has not been processed yet")
    if version.size > MAX_SOURCE_BYTES:
        raise _document_error(
            f"The base schema is limited to {MAX_SOURCE_BYTES // (1024 * 1024)} MB",
            ErrorCode.FILE_TOO_LARGE,
        )
    with storage.open(version.storage_key) as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    try:
        sql = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _document_error("The file is not UTF-8", ErrorCode.INVALID_ENCODING) from None

    script = check(sql)
    if not script.ok:
        raise ApiError(
            422,
            ErrorCode.SCHEMA_PARSE_ERROR,
            "The schema can't be used",
            "Fix the statements below and upload the file again.",
            errors=[
                FieldError(
                    path=(
                        f"statement[{p.statement_index}]"
                        if p.statement_index is not None
                        else "document_id"
                    ),
                    message=f"{p.code.value}: {p.message}",
                )
                for p in script.problems
            ],
        )

    row = WorkflowSchemaRepository(session, ctx).add_version(
        {
            "id": uuid.uuid4(),
            "tenant_id": ctx.tenant_id,
            "project_id": project_id,
            "workflow_id": workflow_id,
            "source": SchemaSource.UPLOADED,
            "source_document_id": put.document_id,
            "storage_key": version.storage_key,
            "sha256": version.sha256,
            "tables": sorted(script.tables()),
            "status": GateStatus.PENDING,
            "findings": [],
            "created_by": str(user.user_id),
        }
    )
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action="SCHEMA_SQL_SOURCE_SET",
        entity_type="workflow",
        entity_id=str(workflow_id),
        after={"version": row.version, "document_id": str(put.document_id), "sha256": row.sha256},
        correlation_id=correlation_id_var.get() or "unknown",
    )
    return body(row, storage)
