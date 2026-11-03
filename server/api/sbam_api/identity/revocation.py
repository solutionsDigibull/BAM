"""Signed-out sessions (A-32, #458): the API refuses a token whose `sid` signed out.

`POST /auth/logout` (the portal calls it just before the Keycloak end-session redirect) records the
caller's `sid`. Every request then asks `is_revoked(sid)`: one indexed primary-key read, no extra
state per user, and another session of the same user (another `sid`) is untouched. A row lives
`REVOCATION_TTL`, which must be at least the longest access-token lifespan Keycloak issues (300 s
here), and `revoke` sweeps the expired ones, so the table never grows past an hour of sign-outs.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.identity import repository

REVOCATION_TTL: Final = timedelta(hours=1)


class SqlSessionRevocations:
    def __init__(
        self,
        sessions: Callable[[], sessionmaker[Session]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.clock = clock

    def is_revoked(self, sid: str) -> bool:
        with self.sessions()() as session:
            return repository.session_is_revoked(session, sid, self.clock())

    def revoke(self, sid: str, keycloak_sub: str) -> None:
        """Idempotent: signing out twice is the same as once."""
        now = self.clock()
        with self.sessions()() as session:
            repository.sweep_revoked_sessions(session, now)
            repository.add_revoked_session(session, sid, keycloak_sub, now + REVOCATION_TTL)
            try:
                session.commit()
            except IntegrityError:  # a parallel sign-out of the same session got there first
                session.rollback()
