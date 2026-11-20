"""Template lists (#782): `GET/PUT /admin/master-lists/{key}`.

Employees, Customers, Assets and Systems are not masters: each is one table of a tenant, replaced
as a whole (`PUT`, `If-Match` = its ETag; `"0"` before the first save). A list never saved answers
its stock headers, no rows, ETag "0". An unknown key is a 404 like a missing resource.
"""

import csv
import io

from sbam_contracts.api import ErrorCode
from sbam_contracts.masters_v03 import LIST_DEFAULT_HEADERS, MasterList, MasterListKey
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, etag, not_found
from sbam_api.db.repository import StaleRowVersion, TenantScopedRepository
from sbam_api.masters.models import MasterListRow
from sbam_api.masters.service import _correlation, version_conflict


class MasterListRepository(TenantScopedRepository[MasterListRow]):
    model = MasterListRow

    def by_key(self, key: MasterListKey, lock: bool = False) -> MasterListRow | None:
        query = self._scoped().where(MasterListRow.key == key.value)
        if lock:  # Postgres; SQLite ignores it
            query = query.with_for_update()
        return self.session.scalars(query).one_or_none()


def list_key(key: str) -> MasterListKey:
    try:
        return MasterListKey(key)
    except ValueError:
        raise not_found() from None


def get_list(session: Session, user: CurrentUser, key: str) -> tuple[MasterList, int]:
    """(the list, its row_version); 0 for a list never saved."""
    which = list_key(key)
    row = MasterListRepository(session, user.context()).by_key(which)
    if row is None:
        return MasterList(headers=list(LIST_DEFAULT_HEADERS[which])), 0
    return MasterList(headers=row.headers, rows=row.rows), row.row_version


def put_list(
    session: Session, user: CurrentUser, key: str, body: MasterList, if_match: str | None
) -> tuple[MasterList, int]:
    which = list_key(key)
    repo = MasterListRepository(session, user.context())
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    row = repo.by_key(which, lock=True)
    if if_match.strip().removeprefix("W/") != etag(row.row_version if row else 0):
        raise version_conflict()
    try:
        if row is None:
            row = repo.add(
                MasterListRow(
                    key=which.value,
                    headers=body.headers,
                    rows=body.rows,
                    created_by=user.keycloak_sub,
                )
            )
        else:
            row.headers, row.rows = body.headers, body.rows
            repo.update(row, row.row_version)
        record(
            session,
            tenant_id=row.tenant_id,
            actor_id=str(user.user_id),
            action="MASTER_LIST_REPLACED",
            entity_type="master_list",
            entity_id=str(row.id),
            after={"key": which.value, "columns": len(body.headers), "rows": len(body.rows)},
            correlation_id=_correlation(),
        )
        session.commit()
    except (IntegrityError, StaleRowVersion):  # a concurrent first save or replace won
        session.rollback()
        raise version_conflict() from None
    return MasterList(headers=row.headers, rows=row.rows), row.row_version


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def to_csv(body: MasterList) -> str:
    """The list as CSV. A cell that a spreadsheet would run as a formula gets a leading `'`."""

    def safe(cell: str) -> str:
        return f"'{cell}" if cell.startswith(_FORMULA_START) else cell

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([safe(h) for h in body.headers])
    writer.writerows([safe(c) for c in row] for row in body.rows)
    return out.getvalue()
