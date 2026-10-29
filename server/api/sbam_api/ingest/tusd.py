"""tusd's hook request and response (tusd v2 hook format). tusd's own wire format, not an SBAM
contract, so it lives here and is not exported to the client."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sbam_contracts.api import ErrorCode


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class HookUpload(_Wire):
    id: str = Field(default="", alias="ID")
    size: int | None = Field(default=None, alias="Size")
    size_is_deferred: bool = Field(default=False, alias="SizeIsDeferred")
    offset: int = Field(default=0, alias="Offset")
    meta_data: dict[str, str] = Field(default_factory=dict, alias="MetaData")


class HookEvent(_Wire):
    upload: HookUpload = Field(default_factory=HookUpload, alias="Upload")


class HookRequest(_Wire):
    type: str = Field(alias="Type")
    event: HookEvent = Field(default_factory=HookEvent, alias="Event")


def accept(upload_id: str | None = None, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    """Let the upload go on. A pre-create answer can choose the upload id and replace the stored
    metadata: what the client sent (the upload token included) is dropped, because tusd echoes the
    metadata to anyone who asks with a HEAD (A-33)."""
    change: dict[str, Any] = {}
    if upload_id:
        change["ID"] = upload_id
    if metadata is not None:
        change["MetaData"] = metadata
    return {"ChangeFileInfo": change} if change else {}


def reject(status: int, code: ErrorCode, detail: str, correlation_id: str) -> dict[str, Any]:
    """Refuse the upload; tusd sends `status` and this problem+json body to the client."""
    problem = {
        "type": "about:blank",
        "title": "Upload refused",
        "status": status,
        "detail": detail,
        "code": code.value,
        "correlation_id": correlation_id,
    }
    return {
        "RejectUpload": True,
        "HTTPResponse": {
            "StatusCode": status,
            "Body": json.dumps(problem),
            "Header": {"Content-Type": "application/problem+json"},
        },
    }
