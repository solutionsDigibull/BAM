"""Keycloak follows the `users` row (A-13).

A role change or deactivation commits the row with `idp_sync_pending_at` set, so the tenant lock is
never held across Keycloak HTTP calls. Keycloak is converged after the commit: `converge_user`
makes it match the row (enabled, roles, sessions revoked), which is idempotent, and clears the mark
only if no newer change replaced it. A failure leaves the mark; `converge_pending` (a relay, every
minute) retries it, so the two never stay apart.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sbam_contracts.identity import UserStatus
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import UserAdmin
from sbam_api.core.conventions import ApiError
from sbam_api.identity.models import User
from sbam_api.identity.repository import clear_idp_sync, pending_idp_sync

log = logging.getLogger("sbam_api.identity")

RELAY_MIN_AGE = timedelta(seconds=60)  # younger ones are still being converged by their request
RELAY_BATCH = 100


def converge_user(sessions: sessionmaker[Session], users: UserAdmin, user_id: uuid.UUID) -> bool:
    """Make Keycloak match the user's row. True when it does (or nothing was pending)."""
    with sessions() as session:
        row = session.get(User, user_id)
        if row is None or row.idp_sync_pending_at is None:
            return True
        sub, status, roles = row.keycloak_sub, row.status, list(row.portal_roles)
        stamp, held = row.idp_sync_pending_at, row.idp_synced_roles
    # No connection or lock is held while Keycloak answers.
    deactivated = status == UserStatus.DEACTIVATED
    roles_changed = held is not None and sorted(held) != sorted(roles)
    try:
        users.set_enabled(sub, not deactivated)
        if roles_changed:
            users.set_roles(sub, list(held or []), roles)
        if deactivated or roles_changed:
            users.logout(sub)
    except ApiError:
        log.warning("keycloak sync pending", extra={"target": str(user_id)})
        return False
    with sessions() as session:
        clear_idp_sync(session, user_id, stamp)
        session.commit()
    return True


def converge_pending(
    sessions: sessionmaker[Session], users: UserAdmin, now: datetime
) -> tuple[int, int]:
    """Converge the users whose mark is older than `RELAY_MIN_AGE`. Returns (done, failed). One
    failing user never stops the others; the next run tries again."""
    with sessions() as session:
        ids = pending_idp_sync(session, now - RELAY_MIN_AGE, RELAY_BATCH)
    done = sum(converge_user(sessions, users, user_id) for user_id in ids)
    return done, len(ids) - done
