"""Keyset paging on `id` for the plain lists (A-15b): `limit` (1..100, 25) and a signed `cursor`.

The cursor is HMAC-signed for a scope, so an edited, forged or replayed one (another tenant's, or
another caller's for the platform-wide list) is 400 INVALID_CURSOR and never reads anything. Ids are
UUIDv7, so ordering by `id` is oldest first. A repository returns `limit + 1` rows: the extra one
only says that another page exists.
"""

import uuid
from typing import Protocol

from sbam_contracts.api import ErrorCode

from sbam_api.core.conventions import decode_cursor, encode_cursor, parse_limit
from sbam_api.identity.service import bad_param


class HasId(Protocol):
    id: uuid.UUID


def read_page(
    cursor: str | None, limit: str | int | None, scope: str, secret: bytes
) -> tuple[int, uuid.UUID | None]:
    """(page size, id of the last row of the previous page)."""
    size = parse_limit(limit)
    if not cursor:
        return size, None
    position = decode_cursor(cursor, scope, secret)
    try:
        return size, uuid.UUID(str(position["id"]))
    except (KeyError, ValueError):
        raise bad_param("cursor", "invalid or expired cursor", ErrorCode.INVALID_CURSOR) from None


def cut[R: HasId](
    rows: list[R], size: int, scope: str, secret: bytes
) -> tuple[list[R], str | None]:
    """The page and the cursor of the next one (None on the last page)."""
    if len(rows) <= size:
        return rows, None
    rows = rows[:size]
    return rows, encode_cursor({"id": str(rows[-1].id)}, scope, secret)
