"""Document upload contracts (TD §5.2, §8.5, issue E4.2).

Rules worth knowing
  * `POST /projects/{id}/uploads` takes 1..20 files. Each name is 1..255 characters with no path
    separator, control character or NUL, and is never `.` or `..`. A bad name or size is a 422 that
    points at `files[n].name` / `files[n].size`.
  * A file is at least 1 byte (a 422 otherwise). The 15 MB cap (15728640 bytes, inclusive) is not a
    422: a larger file is rejected per file as FILE_TOO_LARGE (413 when it is the only file).
  * The request is answered per file. If at least one file is accepted: 201 with `accepted` and
    `rejected`. If none is accepted, the first rejection is returned as problem+json (413
    FILE_TOO_LARGE, 409 FILE_LIMIT_REACHED or 415 UNSUPPORTED_TYPE) and nothing is stored.
  * The token is for one project and one user, and expires `expires_at` (at most 15 minutes after
    it was issued). Uploading with it to another project is refused.
  * Scan (E4.3): every upload is scanned before anything reads it. `ScanStatus` is the verdict kept
    on the document version; INFECTED versions are REJECTED with code MALWARE and quarantined.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import DocumentVersionState

MAX_FILE_BYTES: Final = 15 * 1024 * 1024  # 15728640
MAX_ACTIVE_FILES: Final = 20
UPLOAD_TOKEN_TTL_SECONDS: Final = 15 * 60
DOCUMENT_INGESTION: Final = "DocumentIngestion"  # workflow type; id `ingest-<document_version_id>`
PURGE_RETENTION_DAYS: Final = 90  # a deleted document is restorable for this long, then purged
PURGE_DELETED: Final = "PurgeDeleted"  # workflow type of the daily purge (E4.9)
PURGE_SCHEDULE_ID: Final = "purge-deleted"  # Temporal Schedule id (and the workflow id prefix)
PURGE_CRON: Final = "0 2 * * *"  # 02:00 UTC every day

_NO_SEPARATORS_OR_CONTROL = r"^[^/\\\x00-\x1f\x7f-\x9f]+$"

FileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=255, pattern=_NO_SEPARATORS_OR_CONTROL
    ),
]
MimeType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadFile(_Request):
    name: FileName
    size: Annotated[int, Field(ge=1)]  # the 15 MB cap is the service's 413, not a 422
    mime: MimeType

    @field_validator("name")
    @classmethod
    def _not_a_dot_name(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("name cannot be '.' or '..'")
        return value


class UploadRequest(_Request):
    files: Annotated[list[UploadFile], Field(min_length=1, max_length=MAX_ACTIVE_FILES)]


class AcceptedFile(BaseModel):
    name: str
    size: int
    mime: str


class RejectedFile(BaseModel):
    name: str
    code: ErrorCode  # FILE_TOO_LARGE, FILE_LIMIT_REACHED or UNSUPPORTED_TYPE


class UploadToken(BaseModel):
    token: str
    tus_endpoint: str
    expires_at: datetime
    accepted: list[AcceptedFile]
    rejected: list[RejectedFile]


class ScanStatus(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SCAN_FAILED = "SCAN_FAILED"  # the scanner could not be reached or was out of date


class ScanOutcome(BaseModel):
    """Result of the `scan_clamav` activity: the verdict and the signature set that gave it."""

    document_version_id: UUID
    verdict: ScanStatus
    signature_version: str | None = None  # clamd VERSION, e.g. `ClamAV 1.5.4/27800/<date>`


class DocumentOut(BaseModel):
    """A document with the state of its stored file (`GET`, `POST .../restore`)."""

    id: UUID
    title: str
    state: DocumentVersionState
    size: int
    mime: str
    sha256: str
    created_at: datetime
    deleted_at: datetime | None = None  # set while the document is in the 90-day bin
    deleted_by: str | None = None
    purge_after: datetime | None = None  # deleted_at + 90 days


class PurgeTarget(BaseModel):
    """One deleted document that is due for purging (IDs only, like every workflow input)."""

    tenant_id: UUID
    project_id: UUID
    document_id: UUID


class PurgeRequest(BaseModel):
    """Input of the per-document purge activity; `as_of` is the workflow clock (workflow.now())."""

    target: PurgeTarget
    as_of: datetime


class PurgeOutcome(StrEnum):
    PURGED = "PURGED"  # bytes removed, version PURGED
    SKIPPED = "SKIPPED"  # restored, not yet due, or already purged: nothing to do


class PurgeResult(BaseModel):
    """What one `PurgeDeleted` run did. `failed` documents are retried by the next run."""

    purged: int = 0
    skipped: int = 0
    failed: int = 0


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/documents/`."""
    models: dict[str, type[BaseModel]] = {
        "upload_request": UploadRequest,
        "upload_token": UploadToken,
        "scan_outcome": ScanOutcome,
        "document": DocumentOut,
        "purge_target": PurgeTarget,
        "purge_request": PurgeRequest,
        "purge_result": PurgeResult,
    }
    return {stem: contract_schema(model, f"documents/{stem}") for stem, model in models.items()}
