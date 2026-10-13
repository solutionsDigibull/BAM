"""`SecurityAudit` (#48 port) backed by `audit_log` (ADR 0017: #41 plugs in the implementation).

A denial is written in its own short transaction, so it survives the request failing. Writing must
never break the request: if the DB is unreachable, the structured log line still records it.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser, LogSecurityAudit
from sbam_api.core.logging import correlation_id_var

ACCESS_DENIED = "access_denied"
log = logging.getLogger("sbam_api.security")


class AuditLogSecurityAudit:
    def __init__(self, sessions: Callable[[], sessionmaker[Session]]) -> None:
        self._sessions = sessions  # resolved on first denial, so app start needs no database
        self._log = LogSecurityAudit()

    def denied(self, *, actor: CurrentUser, route: str, target: str | None, reason: str) -> None:
        self._log.denied(actor=actor, route=route, target=target, reason=reason)
        try:
            with self._sessions()() as session:
                record(
                    session,
                    tenant_id=actor.tenant_id,
                    actor_id=str(actor.user_id),
                    action=ACCESS_DENIED,
                    entity_type="route",
                    entity_id=target,
                    after={"route": route, "reason": reason},
                    correlation_id=correlation_id_var.get() or "unknown",
                )
                session.commit()
        except Exception as exc:  # the log line above is the fallback record
            log.warning("security audit row not written", extra={"exc_type": type(exc).__name__})

    def event(
        self,
        *,
        actor: str,
        tenant_id: uuid.UUID | None,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        """Identity events (#49): LOGIN, TENANT_CREATED, TENANT_STATUS_CHANGED, email changes."""
        self._log.event(
            actor=actor,
            tenant_id=tenant_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
        try:
            with self._sessions()() as session:
                record(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor,
                    action=action,
                    entity_type=entity,
                    entity_id=entity_id,
                    before=before,
                    after=after,
                    correlation_id=correlation_id_var.get() or "unknown",
                )
                session.commit()
        except Exception as exc:  # the log line above is the fallback record
            log.warning("audit row not written", extra={"exc_type": type(exc).__name__})
