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
"""

from datetime import datetime
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ErrorCode

MAX_FILE_BYTES: Final = 15 * 1024 * 1024  # 15728640
MAX_ACTIVE_FILES: Final = 20
UPLOAD_TOKEN_TTL_SECONDS: Final = 15 * 60
DOCUMENT_INGESTION: Final = "DocumentIngestion"  # workflow type; id `ingest-<document_version_id>`

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


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/documents/`."""
    models: dict[str, type[BaseModel]] = {
        "upload_request": UploadRequest,
        "upload_token": UploadToken,
    }
    return {stem: contract_schema(model, f"documents/{stem}") for stem, model in models.items()}
