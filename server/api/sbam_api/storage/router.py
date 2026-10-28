"""`GET /api/v1/files/{key}?exp=&sig=`: download through a signed URL (E1.6).

The signature is the authorisation: `signed_url` issues it only to a caller in the key's tenant.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from sbam_api.core.auth import internal
from sbam_api.core.conventions import content_disposition, not_found
from sbam_api.core.settings import Settings
from sbam_api.storage.base import CHUNK, StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_api.storage.signing import verify

# The signed URL is the credential, so no user token: `internal` declares that explicitly.
router = APIRouter(tags=["storage"], dependencies=[Depends(internal)])


def backend(request: Request) -> StorageBackend:
    """One backend per app, built on first use from its settings."""
    state = request.app.state
    if getattr(state, "storage", None) is None:
        state.storage = make_backend(state.settings)
    result: StorageBackend = state.storage
    return result


@router.get("/files/{key:path}", response_class=StreamingResponse)
def download(
    key: str,
    exp: int,
    sig: str,
    request: Request,
    name: str | None = None,
    content_type: Annotated[str | None, Query(alias="type")] = None,
) -> StreamingResponse:
    """`name` and `type` (both or neither) are signed with the link: the file is saved and served
    as that, else as a plain attachment named after the key (#402)."""
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    verify(
        secret.get_secret_value() if secret else None,
        key,
        exp,
        sig,
        filename=name,
        content_type=content_type,
    )
    try:
        stream = backend(request).open(key)
    except FileNotFoundError:
        raise not_found() from None

    def body() -> Iterator[bytes]:
        with stream:
            yield from iter(lambda: stream.read(CHUNK), b"")

    headers = {
        "Content-Disposition": content_disposition(name) if name else "attachment",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        body(), media_type=content_type or "application/octet-stream", headers=headers
    )
