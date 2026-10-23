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
  * Parse (E4.5): a loader turns a clean, type-checked version into `doc.md` and `doc.json`
    (`ParsedDocument`) under the tenant and project prefix of the storage, and a `ParseReport`.
    A block says where its text came from: `page` (PDF), `sheet` and `cell` (XLSX, `Sheet1!B4` is
    sheet `Sheet1`, cell `B4`), `line` (text, Markdown, CSV; for DOCX the 1-based ordinal of the
    block in the document). Every block has at least one of them. A version that cannot be parsed
    is PARSE_FAILED with `parse_code` CORRUPT, ENCRYPTED, EMPTY, TIMEOUT or PARSE_ERROR. CORRUPT,
    ENCRYPTED and EMPTY are final (no retry); a TIMEOUT or an infrastructure error is retried.
  * OCR (E4.8): a PDF page whose text layer is shorter than `MIN_TEXT_CHARS`, and every frame of an
    image, is read by the vision model (`sbam-ocr`), one page per call. Such a version stays
    PARSING until the OCR finishes: `ParseOutcome(parsed=False, code=None)` with pages in
    `report.ocr_needed_pages` means "OCR still to do". The model's answer is an `OcrPage`; its text
    is a block with `ocr="llm"`. A page the model cannot read is an empty block with
    `unreadable=True` and is listed in `report.unreadable_pages`; the document is PARSED when at
    least one page is readable, else PARSE_FAILED (EMPTY, or OCR_UNAVAILABLE when the provider was
    down). More than `MAX_OCR_PAGES` pages is PARSE_ERROR.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import DocumentVersionState

MAX_FILE_BYTES: Final = 15 * 1024 * 1024  # 15728640
MAX_ACTIVE_FILES: Final = 20
MAX_ATTACHMENT_DEPTH: Final = 1  # an attachment of an attachment is not followed (E4.6)
UPLOAD_TOKEN_TTL_SECONDS: Final = 15 * 60
DOCUMENT_INGESTION: Final = "DocumentIngestion"  # workflow type; id `ingest-<document_version_id>`
PURGE_RETENTION_DAYS: Final = 90  # a deleted document is restorable for this long, then purged
PURGE_DELETED: Final = "PurgeDeleted"  # workflow type of the daily purge (E4.9)
PURGE_SCHEDULE_ID: Final = "purge-deleted"  # Temporal Schedule id (and the workflow id prefix)
PURGE_CRON: Final = "0 2 * * *"  # 02:00 UTC every day
CLEANUP_UPLOADS: Final = "CleanupAbandonedUploads"  # workflow type of the daily clean-up (E4.12)
CLEANUP_SCHEDULE_ID: Final = "cleanup-abandoned-uploads"  # Temporal Schedule id
CLEANUP_HOUR_UTC: Final = 3  # 03:00 UTC every day, an hour after the purge
# Loader limits (E4.5). A file over a cap is PARSE_FAILED, never silently cut.
MAX_PDF_PAGES: Final = 2000
MAX_XLSX_CELLS: Final = 1_000_000
PARSE_TIMEOUT_SECONDS: Final = 10 * 60  # per file
PARSE_HEARTBEAT_SECONDS: Final = 60
PARSE_MEMORY_LIMIT_BYTES: Final = 1024 * 1024 * 1024  # the ingest worker container's cap
# OCR by vision model (E4.8). A PDF page whose text layer is shorter than MIN_TEXT_CHARS is a scan.
MIN_TEXT_CHARS: Final = 20
MAX_OCR_PAGES: Final = 200  # pages (or image frames) one document may send to the model
OCR_DPI: Final = 200
OCR_MAX_SIDE_PX: Final = 3000  # longest side of a page image
OCR_MAX_IMAGE_BYTES: Final = 5 * 1024 * 1024  # one page image (PNG)
OCR_TIMEOUT_SECONDS: Final = 30 * 60  # per document: up to 200 model calls
OCR_HEARTBEAT_SECONDS: Final = 60
OCR_PAGE_ATTEMPTS: Final = 3  # a page whose answer never validates is unreadable after this many

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


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    ROW = "row"  # one CSV row, as text; the same rows are in the table
    CELL = "cell"  # one spreadsheet cell with a value
    TABLE = "table"  # a table in the source; `table` is its index in `ParsedDocument.tables`


class Block(BaseModel):
    """One piece of the document with where it came from."""

    model_config = ConfigDict(extra="forbid")

    type: BlockType
    text: str
    level: int | None = Field(default=None, ge=1, le=6)  # headings only
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = Field(default=None, min_length=1, max_length=255)
    cell: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}[1-9][0-9]*$")
    line: int | None = Field(default=None, ge=1)
    table: int | None = Field(default=None, ge=0)
    # E4.8: the text of this page was read from its image by the vision model, not from the file.
    ocr: Literal["llm"] | None = None
    # E4.8: the model could not read this page (the block's text is empty). The page is also in
    # `ParseReport.unreadable_pages`.
    unreadable: bool = False

    @model_validator(mode="after")
    def _says_where_it_came_from(self) -> "Block":
        if self.page is None and self.sheet is None and self.cell is None and self.line is None:
            raise ValueError("a block needs page, sheet, cell or line")
        if self.cell is not None and self.sheet is None and self.page is None:
            raise ValueError("a cell needs its sheet")
        if self.type == BlockType.TABLE and self.table is None:
            raise ValueError("a table block names its table")
        if self.unreadable and (self.text or self.page is None or self.ocr is None):
            raise ValueError("an unreadable block is an empty OCR block of a page")
        return self


class Table(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = Field(default=None, min_length=1, max_length=255)
    range: str | None = None  # a spreadsheet range such as `A1:D20`
    start_line: int | None = Field(default=None, ge=1)  # a CSV: the line of the header


class ParsedDocument(BaseModel):
    """`doc.json`: what a loader read from a document version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    document_version_id: UUID
    loader: str = Field(min_length=1, max_length=40)  # the loader id of the Filetype row
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)


class AttachmentOutcome(StrEnum):
    """What became of one attachment of an e-mail (E4.6)."""

    REGISTERED = "REGISTERED"  # a child document, scanned and parsed like any upload
    FILE_LIMIT_REACHED = "FILE_LIMIT_REACHED"  # the project already holds 20 active files
    TOO_LARGE = "TOO_LARGE"  # over 15 MB: left out
    TOO_DEEP = "TOO_DEEP"  # an attachment of an attachment: only one level is followed


class AttachmentReport(BaseModel):
    """One attachment of a parsed e-mail, in `ParseReport.attachments` of the parent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=255)
    size: int = Field(ge=0)
    outcome: AttachmentOutcome
    # The child document (`DocumentOut.parent_document_id` is this version's document). Set for
    # REGISTERED, and for FILE_LIMIT_REACHED where the child is kept as a REJECTED document.
    document_id: UUID | None = None


class ParseReport(BaseModel):
    """Kept on the document version (`parse_report`) after every parse attempt."""

    model_config = ConfigDict(extra="forbid")

    pages: int = Field(default=0, ge=0)  # PDF
    sheets: int = Field(default=0, ge=0)  # XLSX
    tables: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    ocr_needed_pages: list[int] = Field(
        default_factory=list
    )  # PDF pages without a text layer, and every frame of an image (E4.8)
    ocr_pages: list[int] = Field(default_factory=list)  # of those, the ones the model read
    unreadable_pages: list[int] = Field(default_factory=list)  # and the ones it could not read
    attachments: list[AttachmentReport] = Field(default_factory=list)  # e-mail only (E4.6)


class ParseOutcome(BaseModel):
    """Result of the `parse_document` activity. `code` is set when the version is PARSE_FAILED."""

    document_version_id: UUID
    parsed: bool
    code: ErrorCode | None = None
    report: ParseReport | None = None


class OcrPage(BaseModel):
    """What the vision model answers for one page (`sbam-ocr`). Nothing else is accepted: a model
    that adds a field (`status`, `approved`, ...) is re-asked and the answer is never stored."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)  # the page that was sent; an answer for another page is invalid
    markdown: str = Field(max_length=200_000)  # the page's text as Markdown; empty when unreadable
    unreadable: bool = False  # the page is blank, noise, or too poor to read


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
    # Why the document is REJECTED: MALWARE (the scan), or TYPE_MISMATCH / UNSUPPORTED_TYPE /
    # INVALID_ENCODING (type detection, E4.4). None for every other state.
    reject_code: ErrorCode | None = None
    # Why a PARSE_FAILED document failed (CORRUPT, ENCRYPTED, EMPTY, TIMEOUT, PARSE_ERROR) and what
    # the loader found (E4.5). None until a parse attempt has finished.
    parse_code: ErrorCode | None = None
    parse_report: ParseReport | None = None
    # E4.6: the e-mail this document is an attachment of. None for an upload; one level only.
    parent_document_id: UUID | None = None


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


class CleanupResult(BaseModel):
    """What one `CleanupAbandonedUploads` run did (E4.12). `failed` files are retried next run."""

    files_removed: int = 0  # staged tusd files (and their .info) of uploads that never finished
    rows_deleted: int = 0  # abandoned upload reservations past their retention
    orphans: int = 0  # staged files no reservation owns: reported, never deleted
    failed: int = 0  # files that could not be removed


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/documents/`."""
    models: dict[str, type[BaseModel]] = {
        "upload_request": UploadRequest,
        "upload_token": UploadToken,
        "scan_outcome": ScanOutcome,
        "parsed_document": ParsedDocument,
        "parse_report": ParseReport,
        "parse_outcome": ParseOutcome,
        "ocr_page": OcrPage,
        "document": DocumentOut,
        "purge_target": PurgeTarget,
        "purge_request": PurgeRequest,
        "purge_result": PurgeResult,
        "cleanup_result": CleanupResult,
    }
    return {stem: contract_schema(model, f"documents/{stem}") for stem, model in models.items()}
