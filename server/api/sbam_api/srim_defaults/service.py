"""Admin SRIM defaults (E9.1b). The router only maps HTTP onto these.

* The tenant comes from the caller's token, never from the body.
* The ETag is the row's `row_version`; a tenant that never set values has no row and reads as
  version 0, so the first PUT sends `If-Match: "0"` like any other write.
* A PUT replaces all six blocks (the contract requires them all), writes a SRIM_DEFAULTS_UPDATED
  audit row with the blocks before and after, and affects manifests built afterwards only: a
  stored manifest is never rewritten.
"""

from typing import Any

from sbam_contracts.manifest import ISA14_SRIM_DEFAULTS, SrimDefaultsView, SrimRuntimeDefaults
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import check_if_match
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import StaleRowVersion
from sbam_api.srim_defaults.models import TenantSrimDefaults
from sbam_api.srim_defaults.repository import SrimDefaultsRepository


def get(session: Session, user: CurrentUser) -> tuple[SrimDefaultsView, int]:
    """The view and the row version for the ETag (0 while no row exists)."""
    repo = SrimDefaultsRepository(session, user.context())
    defaults, is_default = repo.current()
    row = repo.row()
    return SrimDefaultsView(defaults=defaults, is_default=is_default), (
        0 if row is None else (row.row_version)
    )


def put(
    session: Session, user: CurrentUser, body: SrimRuntimeDefaults, if_match: str | None
) -> tuple[SrimDefaultsView, int]:
    repo = SrimDefaultsRepository(session, user.context())
    row = repo.row(for_update=True)  # two admins saving at once take turns
    check_if_match(if_match, 0 if row is None else row.row_version)
    before: dict[str, Any] | None = None if row is None else dict(row.defaults)
    after = body.model_dump(mode="json")
    if row is not None and before == after:
        return SrimDefaultsView(defaults=body, is_default=False), row.row_version  # nothing to do
    try:
        if row is None:
            row = repo.add(TenantSrimDefaults(defaults=after, created_by=str(user.user_id)))
        else:
            row.defaults = after
            repo.update(row, row.row_version)
        record(
            session,
            tenant_id=row.tenant_id,
            actor_id=str(user.user_id),
            action="SRIM_DEFAULTS_UPDATED",
            entity_type="tenant_srim_defaults",
            entity_id=str(row.id),
            before=before,
            after=after,
            correlation_id=correlation_id_var.get() or "unknown",
        )
        session.commit()
    except IntegrityError:  # both saw "no row"; the other writer's insert won
        session.rollback()
        raise StaleRowVersion() from None
    session.refresh(row)
    return SrimDefaultsView(defaults=body, is_default=False), row.row_version


def reset(
    session: Session, user: CurrentUser, if_match: str | None
) -> tuple[SrimDefaultsView, int]:
    """Back to the ISA14 values: the tenant's row is deleted (the audit row keeps what it held)."""
    repo = SrimDefaultsRepository(session, user.context())
    row = repo.row(for_update=True)
    check_if_match(if_match, 0 if row is None else row.row_version)
    if row is not None:
        record(
            session,
            tenant_id=row.tenant_id,
            actor_id=str(user.user_id),
            action="SRIM_DEFAULTS_RESET",
            entity_type="tenant_srim_defaults",
            entity_id=str(row.id),
            before=dict(row.defaults),
            after=None,
            correlation_id=correlation_id_var.get() or "unknown",
        )
        session.delete(row)
        session.commit()
    return SrimDefaultsView(defaults=ISA14_SRIM_DEFAULTS, is_default=True), 0
