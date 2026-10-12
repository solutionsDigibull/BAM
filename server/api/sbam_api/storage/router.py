"""`GET /api/v1/files/{key}?exp=&sig=`: download through a signed URL (E1.6).

The signature is the authorisation: `signed_url` issues it only to a caller in the key's tenant.
"""

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from sbam_api.core.auth import internal
from sbam_api.core.conventions import not_found
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
def download(key: str, exp: int, sig: str, request: Request) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    verify(secret.get_secret_value() if secret else None, key, exp, sig)
    try:
        stream = backend(request).open(key)
    except FileNotFoundError:
        raise not_found() from None

    def body() -> Iterator[bytes]:
        with stream:
            yield from iter(lambda: stream.read(CHUNK), b"")

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment", "Cache-Control": "private, no-store"},
    )
