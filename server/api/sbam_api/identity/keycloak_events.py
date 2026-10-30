"""Keycloak's own events in SBAM's audit log (A-18b, #541).

Keycloak records failed logins, lockouts, password and credential changes (events) and every admin
console / REST change such as a role granted or a flow edited (admin events). They stay inside
Keycloak unless something copies them. A worker Schedule reads them through the `sbam-api-admin`
service account (it needs `view-events`) and writes one `audit_log` row per event, so a burst of
failed admin sign-ins, or a role granted in the console, is visible next to SBAM's own audit trail.

* Idempotent: Keycloak gives every event an id, and that id is the audit row's `entity_id`; an
  event already written is skipped, so reading the same window twice, or a crash half way, writes
  each event once. Each source's window starts at its newest row written minus `OVERLAP` (a day at
  the first run), so a worker that was down catches up.
* What is NOT copied, on purpose: the `username` Keycloak records for a failed login (a person
  mistypes their password into that box), event `details`, and the raw representation of an admin
  event (a user's attributes, credentials). A client address is cut to its /24 (IPv4) or /48 (IPv6).
  Role changes carry the role names only.
* A flood of failed logins cannot become a flood of audit rows: at most `MAX_PER_SOURCE` per
  (user or address) per run are written and one `KEYCLOAK_EVENTS_SUPPRESSED` row counts the rest.
* The tenant is the user's (the users mirror) or, for an admin event about a user, that user's;
  rows about an unknown user or a super-admin have none.
"""

import ipaddress
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import httpx
from sqlalchemy.orm import Session

from sbam_api.audit.repository import known_entity_ids, last_written, record
from sbam_api.core.ids import uuid7
from sbam_api.identity.keycloak_admin import TIMEOUT_SECONDS, _AdminApi
from sbam_api.identity.repository import tenants_of_subs
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID

EVENT_ENTITY: Final = "keycloak_event"
ADMIN_ENTITY: Final = "keycloak_admin_event"
SUPPRESSED_ACTION: Final = "KEYCLOAK_EVENTS_SUPPRESSED"
# What is worth an audit row. Plain LOGIN / LOGOUT / token refresh are volume, not security events.
EVENT_TYPES: Final = (
    "LOGIN_ERROR",
    "CODE_TO_TOKEN_ERROR",
    "USER_DISABLED_BY_PERMANENT_LOCKOUT",
    "USER_DISABLED_BY_TEMPORARY_LOCKOUT",
    "RESET_PASSWORD",
    "RESET_PASSWORD_ERROR",
    "UPDATE_PASSWORD",
    "UPDATE_PASSWORD_ERROR",
    "UPDATE_TOTP",
    "REMOVE_TOTP",
    "UPDATE_CREDENTIAL",
    "REMOVE_CREDENTIAL",
)
FIRST_WINDOW: Final = timedelta(days=1)
OVERLAP: Final = timedelta(minutes=10)
PAGE: Final = 200
MAX_READ: Final = 5_000  # per source per run; a longer backlog is read by the next runs
MAX_PER_SOURCE: Final = 5  # LOGIN_ERROR rows per user-or-address per run
MAX_ROLES: Final = 20
log = logging.getLogger("sbam_api.identity.keycloak_events")


class EventSource(Protocol):
    def events(self, since_ms: int) -> list[dict[str, Any]]: ...

    def admin_events(self, since_ms: int) -> list[dict[str, Any]]: ...


class KeycloakEvents(_AdminApi):
    """Read access to the realm's events; any failure raises `httpx.HTTPError` to the caller."""

    def _pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self._client() as client:
            while len(out) < MAX_READ:
                res = client.get(
                    f"{self.admin_url}{path}",
                    params={**params, "first": len(out), "max": PAGE},
                    timeout=TIMEOUT_SECONDS * 4,
                )
                res.raise_for_status()
                page: list[dict[str, Any]] = res.json()
                out += page
                if len(page) < PAGE:
                    break
        return out

    def events(self, since_ms: int) -> list[dict[str, Any]]:
        return self._pages("/events", {"type": list(EVENT_TYPES), "dateFrom": str(since_ms)})

    def admin_events(self, since_ms: int) -> list[dict[str, Any]]:
        return self._pages("/admin-events", {"dateFrom": str(since_ms)})


@dataclass
class ImportResult:
    read: int = 0
    written: int = 0
    already: int = 0
    suppressed: int = 0
    failed_sources: list[str] = field(default_factory=list)


def ip_class(address: str | None) -> str | None:
    """The address cut to its /24 (IPv4) or /48 (IPv6): enough to see a source, not a person."""
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    bits = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False))


def _role_names(representation: str | None) -> list[str]:
    if not representation:
        return []
    try:
        parsed = json.loads(representation)
    except ValueError:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return sorted({str(r["name"])[:100] for r in parsed if isinstance(r, dict) and "name" in r})[
        :MAX_ROLES
    ]


def _when(event: dict[str, Any]) -> str:
    return datetime.fromtimestamp(int(event["time"]) / 1000, UTC).isoformat()


def event_row(event: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], str | None]:
    """(action, actor, entity id, after, the Keycloak user it is about) for one event."""
    kind = str(event["type"])
    action = "KEYCLOAK_LOGIN_FAILED" if kind == "LOGIN_ERROR" else f"KEYCLOAK_{kind}"
    user = event.get("userId")
    after = {
        "type": kind,
        "event_time": _when(event),
        "client": event.get("clientId"),
        "error": event.get("error"),
        "ip": ip_class(event.get("ipAddress")),
    }
    return action, str(user or "keycloak-anonymous"), str(event["id"]), after, user


def admin_row(event: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], str | None]:
    operation, resource = str(event.get("operationType")), str(event.get("resourceType"))
    auth = event.get("authDetails") or {}
    path = str(event.get("resourcePath") or "")
    parts = path.split("/")
    target = parts[1] if len(parts) > 1 and parts[0] == "users" else None
    after: dict[str, Any] = {
        "operation": operation,
        "resource": resource,
        "path": path[:300],
        "event_time": _when(event),
        "ip": ip_class(auth.get("ipAddress")),
    }
    if roles := _role_names(event.get("representation")):
        after["roles"] = roles
    if event.get("error"):
        after["error"] = str(event["error"])[:100]
    action = f"KEYCLOAK_ADMIN_{operation}_{resource}"[:100]
    actor = str(auth.get("userId") or "keycloak-admin")
    return action, actor, str(event["id"]), after, target or auth.get("userId")


def import_keycloak_events(
    session: Session, feed: EventSource, now: datetime, correlation_id: str
) -> ImportResult:
    result = ImportResult()
    batches: list[tuple[str, str, list[dict[str, Any]]]] = []
    for entity, read in ((EVENT_ENTITY, feed.events), (ADMIN_ENTITY, feed.admin_events)):
        # One window per source: a source that could not be read keeps its backlog for the next run.
        newest = last_written(session, [entity])
        since = (newest - OVERLAP) if newest else (now - FIRST_WINDOW)
        since_ms = int(since.timestamp() * 1000)
        try:
            found = [e for e in read(since_ms) if int(e["time"]) >= since_ms]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning(
                "keycloak events not read", extra={"source": entity, "exc": type(exc).__name__}
            )
            result.failed_sources.append(entity)
            continue
        result.read += len(found)
        batches.append((entity, "admin" if entity == ADMIN_ENTITY else "user", found))
    users = {
        sub
        for _, _, found in batches
        for e in found
        for sub in (
            [e.get("userId"), (e.get("authDetails") or {}).get("userId")]
            + [
                p.split("/")[1]
                for p in [str(e.get("resourcePath") or "")]
                if p.startswith("users/")
            ]
        )
        if sub
    }
    tenants = tenants_of_subs(session, users)
    seen_per_source: Counter[str] = Counter()
    for entity, kind, found in batches:
        have = known_entity_ids(session, entity, [str(e["id"]) for e in found])
        for event in sorted(found, key=lambda e: int(e["time"])):
            if str(event["id"]) in have:
                result.already += 1
                continue
            action, actor, entity_id, after, about = (
                admin_row(event) if kind == "admin" else event_row(event)
            )
            if kind == "user" and event["type"] == "LOGIN_ERROR":
                source = str(event.get("userId") or after["ip"] or "unknown")
                seen_per_source[source] += 1
                if seen_per_source[source] > MAX_PER_SOURCE:
                    result.suppressed += 1
                    continue
            record(
                session,
                tenant_id=tenants.get(about or "") or tenants.get(actor),
                actor_id=actor,
                action=action,
                entity_type=entity,
                entity_id=entity_id,
                after=after,
                correlation_id=correlation_id,
            )
            result.written += 1
    if result.suppressed:
        record(
            session,
            tenant_id=None,
            actor_id=SYSTEM_ACTOR_ID,
            action=SUPPRESSED_ACTION,
            entity_type=EVENT_ENTITY,
            entity_id=str(uuid7()),
            after={"suppressed": result.suppressed, "per_source_limit": MAX_PER_SOURCE},
            correlation_id=correlation_id,
        )
    session.commit()
    return result
