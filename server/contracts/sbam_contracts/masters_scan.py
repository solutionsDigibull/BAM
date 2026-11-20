"""Masters scan and import as a Temporal run (#609, N-02b option (a), ADR 0029).

`POST /admin/masters/import` and `POST /admin/master-documents` used to scan the file with ClamAV
and parse it inside the request. A client that sends `Prefer: respond-async` and an
`Idempotency-Key` gets `202 MastersImportAccepted` instead: the request keeps only the cheap checks
(size, type, tenant, role), stores the bytes unscanned in `uploads/` under their hash and starts
`MastersScanImport` (workflow id `masters-scan-<scan_id>`, queue sbam-ingest). The activity scans,
quarantines or parses, and writes, with the same code and the same refusals as the request path.

  * `scan_id` is derived from the tenant and the `Idempotency-Key`, so a repeated key finds the same
    run; the same key for another file is 422 `IDEMPOTENCY_KEY_REUSED`.
  * `GET /admin/masters/scans/{scan_id}` answers `MastersScanStatus` (tenant-scoped, 404 for any
    other tenant): QUEUED, RUNNING, then DONE with the result (the `ImportReport`, or the attached
    document) or FAILED with a `ProblemDetail` (the code the synchronous route would have answered,
    `MALWARE`, `STRUCTURE_MISMATCH`, `SCAN_UNAVAILABLE` after the retries, ...).
  * The run is mirrored in `temporal_runs` (`subject_type` project; the subject is the masters
    project, or the all-zero id for the tenant-wide masters). No new table.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ErrorCode, ProblemDetail
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import ImportMode, ImportReport, MasterDocumentOut
from sbam_contracts.runs import Run, RunInput

MASTERS_SCAN_IMPORT: Final = "MastersScanImport"  # workflow type
PREFER_ASYNC: Final = "respond-async"  # RFC 7240 `Prefer` token that selects the 202 answer


def masters_scan_workflow_id(scan_id: UUID) -> str:
    """One run per scan id, so a repeated `Idempotency-Key` starts nothing new."""
    return f"masters-scan-{scan_id}"


class MastersScanKind(StrEnum):
    IMPORT = "IMPORT"  # the masters workbook
    DOCUMENT = "DOCUMENT"  # a master document (context file)


class MastersScanState(StrEnum):
    QUEUED = "QUEUED"  # accepted, no worker has picked it up yet
    RUNNING = "RUNNING"  # scanning, parsing or writing
    DONE = "DONE"
    FAILED = "FAILED"


class MastersImportAccepted(BaseModel):
    """`202 Accepted` body of the two masters upload routes when the client asked to respond
    asynchronously. Poll `GET /admin/masters/scans/{scan_id}`."""

    model_config = ConfigDict(extra="forbid")

    scan_id: UUID
    kind: MastersScanKind
    state: MastersScanState
    run: Run


class MastersScanStatus(BaseModel):
    """The state of one scan, and its result once DONE (exactly one of `import_report` /
    `document` for the kind) or its problem once FAILED."""

    model_config = ConfigDict(extra="forbid")

    scan_id: UUID
    kind: MastersScanKind
    state: MastersScanState
    run: Run | None = None
    import_report: ImportReport | None = None
    document: MasterDocumentOut | None = None
    created: bool | None = None  # DOCUMENT: false when the same file was already attached
    error: ProblemDetail | None = None
    created_at: datetime
    updated_at: datetime


# --- Workflow / activity DTOs (worker-internal) -------------------------------------------------


class MastersScanInput(RunInput):
    """`MastersScanImport`'s input: ids and keys only. The bytes wait in `uploads/pending_key`."""

    scan_id: UUID
    kind: MastersScanKind
    pending_key: Annotated[str, Field(min_length=1, max_length=300)]
    sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    filename: Annotated[str, Field(max_length=255)]
    masters_project_id: UUID | None = None  # the project the masters belong to; null = tenant-wide
    mode: ImportMode | None = None  # IMPORT
    master_type: MasterType | None = None  # DOCUMENT: the Admin's route override
    user_id: UUID
    keycloak_sub: Annotated[str, Field(min_length=1, max_length=255)]


class MastersScanResult(BaseModel):
    """What the activity answers: the outcome is stored (audit row), this is its short form."""

    model_config = ConfigDict(extra="forbid")

    scan_id: UUID
    state: MastersScanState
    code: ErrorCode | None = None  # FAILED: the problem's code
    document_id: UUID | None = None  # DOCUMENT, DONE
    parse_pending: bool = False  # DOCUMENT: start `MasterDocumentIngestion` for `document_id`


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/masters_scan/`."""
    models: dict[str, type[BaseModel]] = {
        "accepted": MastersImportAccepted,
        "status": MastersScanStatus,
    }
    return {stem: contract_schema(model, f"masters_scan/{stem}") for stem, model in models.items()}
