"""A-18b (#541): Keycloak's events become audit rows once each, without anything a person typed."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.keycloak_events import (
    ADMIN_ENTITY,
    EVENT_ENTITY,
    MAX_PER_SOURCE,
    KeycloakEvents,
    import_keycloak_events,
    ip_class,
)
from sbam_api.identity.models import Tenant, User
from sqlalchemy import Engine, pool, select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
TENANT = uuid.uuid4()
SUB = str(uuid.uuid4())
ADMIN_SUB = str(uuid.uuid4())


def ms(minutes_ago: float) -> int:
    return int((NOW - timedelta(minutes=minutes_ago)).timestamp() * 1000)


def engine() -> Engine:
    eng = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    for table in (Tenant.__table__, User.__table__, AuditLog.__table__):
        table.create(eng)  # type: ignore[attr-defined]
    return eng


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    made = make_sessionmaker(engine())
    with made() as s:
        s.add(Tenant(id=TENANT, name="T"))
        s.flush()
        s.add(User(tenant_id=TENANT, keycloak_sub=SUB, email="u@x.test", portal_roles=["sbam-fde"]))
        s.commit()
    return made


class Feed:
    """A stand-in for `KeycloakEvents`: what Keycloak holds, served the way it filters by time."""

    def __init__(self, events: list[dict[str, Any]], admin: list[dict[str, Any]] | None = None):
        self.stored, self.admin_stored = events, admin or []
        self.fail_admin = False
        self.asked: list[tuple[str, int]] = []

    def events(self, since_ms: int) -> list[dict[str, Any]]:
        self.asked.append(("events", since_ms))
        return [e for e in self.stored if int(e["time"]) >= since_ms]

    def admin_events(self, since_ms: int) -> list[dict[str, Any]]:
        self.asked.append(("admin", since_ms))
        if self.fail_admin:
            raise httpx.ConnectError("down")
        return [e for e in self.admin_stored if e["time"] >= since_ms]


def login_error(
    n: int = 1, minutes_ago: float = 5, user: str | None = SUB, **kw: Any
) -> dict[str, Any]:
    return {
        "id": f"ev-{n}",
        "time": ms(minutes_ago),
        "type": "LOGIN_ERROR",
        "clientId": "sbam-admin",
        "userId": user,
        "ipAddress": "203.0.113.77",
        "error": "invalid_user_credentials",
        "details": {"username": "my-secret-Passw0rd!", "auth_method": "openid-connect"},
        **kw,
    }


def role_change(n: int = 1, minutes_ago: float = 4) -> dict[str, Any]:
    return {
        "id": f"adm-{n}",
        "time": ms(minutes_ago),
        "operationType": "CREATE",
        "resourceType": "REALM_ROLE_MAPPING",
        "resourcePath": f"users/{SUB}/role-mappings/realm",
        "authDetails": {"userId": ADMIN_SUB, "ipAddress": "2001:db8:abcd:1234::5", "clientId": "c"},
        "representation": json.dumps(
            [
                {
                    "id": "r1",
                    "name": "sbam-admin",
                    "description": "Tenant admin",
                    "attributes": {"k": "v"},
                }
            ]
        ),
    }


def rows(s: sessionmaker[Session]) -> list[AuditLog]:
    with s() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.action, AuditLog.entity_id)))


def run(s: sessionmaker[Session], feed: Any, now: datetime = NOW) -> Any:
    with s() as session:
        return import_keycloak_events(session, feed, now, "c-test")


def test_a18b_t01_a_failed_login_becomes_one_audit_row_with_the_tenant(
    sessions: sessionmaker[Session],
) -> None:
    result = run(sessions, Feed([login_error()]))
    [row] = rows(sessions)
    assert (result.read, result.written) == (1, 1)
    assert (row.action, row.entity_type, row.entity_id) == (
        "KEYCLOAK_LOGIN_FAILED",
        EVENT_ENTITY,
        "ev-1",
    )
    assert row.tenant_id == TENANT and row.actor_id == SUB
    assert row.after is not None
    assert row.after["client"] == "sbam-admin" and row.after["error"] == "invalid_user_credentials"
    assert row.after["ip"] == "203.0.113.0/24"  # the source, not the person


def test_a18b_t02_nothing_a_person_typed_is_copied(sessions: sessionmaker[Session]) -> None:
    run(sessions, Feed([login_error()], [role_change()]))
    dump = json.dumps(
        [[r.actor_id, r.action, r.entity_id, r.after, r.before] for r in rows(sessions)]
    )
    for forbidden in (
        "my-secret-Passw0rd!",
        "username",
        "203.0.113.77",
        "Tenant admin",
        "attributes",
        "r1",
    ):
        assert forbidden not in dump, forbidden


def test_a18b_t03_a_role_change_carries_the_role_names_and_the_users_tenant(
    sessions: sessionmaker[Session],
) -> None:
    run(sessions, Feed([], [role_change()]))
    [row] = rows(sessions)
    assert (
        row.action == "KEYCLOAK_ADMIN_CREATE_REALM_ROLE_MAPPING" and row.entity_type == ADMIN_ENTITY
    )
    assert row.actor_id == ADMIN_SUB and row.tenant_id == TENANT  # the tenant of the user changed
    assert row.after is not None
    assert row.after["roles"] == ["sbam-admin"] and row.after["ip"] == "2001:db8:abcd::/48"
    assert row.after["path"] == f"users/{SUB}/role-mappings/realm"


def test_a18b_t04_reading_the_same_window_twice_writes_each_event_once(
    sessions: sessionmaker[Session],
) -> None:
    feed = Feed([login_error(1), login_error(2)], [role_change()])
    assert run(sessions, feed).written == 3
    again = run(sessions, feed)
    assert (again.written, again.already) == (0, 3) and len(rows(sessions)) == 3


def test_a18b_t05_a_crash_half_way_loses_nothing_and_duplicates_nothing(
    sessions: sessionmaker[Session],
) -> None:
    events = [login_error(n) for n in range(1, 4)]
    with sessions() as session:
        original = session.commit

        def boom() -> None:
            raise RuntimeError("worker killed before the commit")

        session.commit = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            import_keycloak_events(session, Feed(events), NOW, "c-crash")
        session.rollback()
        session.commit = original  # type: ignore[method-assign]
    assert rows(sessions) == []  # nothing half written
    assert run(sessions, Feed(events)).written == 3


def test_a18b_t06_a_worker_that_was_down_catches_up_from_its_last_row(
    sessions: sessionmaker[Session],
) -> None:
    feed = Feed([login_error(1, minutes_ago=300)])
    run(sessions, feed, now=NOW - timedelta(minutes=290))  # imported while it was up
    feed.stored.append(login_error(2, minutes_ago=100))  # happened while the worker was down
    feed.stored.append(login_error(3, minutes_ago=1))
    result = run(sessions, feed)
    assert result.written == 2
    assert {r.entity_id for r in rows(sessions)} == {"ev-1", "ev-2", "ev-3"}


def test_a18b_t07_the_first_run_looks_back_a_day_not_forever(
    sessions: sessionmaker[Session],
) -> None:
    feed = Feed([login_error(1, minutes_ago=60 * 30), login_error(2, minutes_ago=60 * 2)])
    assert run(sessions, feed).written == 1
    assert [r.entity_id for r in rows(sessions)] == ["ev-2"]


def test_a18b_t08_a_source_that_cannot_be_read_keeps_its_backlog(
    sessions: sessionmaker[Session],
) -> None:
    feed = Feed([login_error(1)], [role_change(1, minutes_ago=30)])
    feed.fail_admin = True
    first = run(sessions, feed)
    assert first.failed_sources == [ADMIN_ENTITY] and first.written == 1
    feed.fail_admin = False
    second = run(
        sessions, feed, now=NOW + timedelta(hours=1)
    )  # login events moved the other cursor
    assert second.written == 1 and second.failed_sources == []
    assert {r.entity_type for r in rows(sessions)} == {EVENT_ENTITY, ADMIN_ENTITY}


def test_a18b_t09_a_flood_of_failed_logins_is_capped_and_counted(
    sessions: sessionmaker[Session],
) -> None:
    flood = [login_error(n, minutes_ago=5) for n in range(1, 101)]
    result = run(sessions, Feed(flood))
    kinds = [r.action for r in rows(sessions)]
    assert result.written == MAX_PER_SOURCE and result.suppressed == 100 - MAX_PER_SOURCE
    assert kinds.count("KEYCLOAK_LOGIN_FAILED") == MAX_PER_SOURCE
    [summary] = [r for r in rows(sessions) if r.action == "KEYCLOAK_EVENTS_SUPPRESSED"]
    assert summary.after is not None and summary.after["suppressed"] == 100 - MAX_PER_SOURCE


def test_a18b_t10_the_cap_is_per_user_or_address_not_global(
    sessions: sessionmaker[Session],
) -> None:
    other = str(uuid.uuid4())
    events = [login_error(n) for n in range(1, 11)] + [
        login_error(n, user=other) for n in range(11, 21)
    ]
    assert run(sessions, Feed(events)).written == 2 * MAX_PER_SOURCE


def test_a18b_t11_unknown_users_and_super_admins_have_no_tenant_and_no_crash(
    sessions: sessionmaker[Session],
) -> None:
    run(sessions, Feed([login_error(1, user=None), login_error(2, user="not-a-known-user")]))
    got = rows(sessions)
    assert len(got) == 2 and all(r.tenant_id is None for r in got)
    assert {r.actor_id for r in got} == {"keycloak-anonymous", "not-a-known-user"}


def test_a18b_t12_an_odd_event_is_skipped_not_fatal(sessions: sessionmaker[Session]) -> None:
    junk = {"id": "x", "time": "soon", "type": "LOGIN_ERROR"}
    result = run(sessions, Feed([junk, login_error(2)]))
    assert result.failed_sources == [EVENT_ENTITY] and result.written == 0  # unreadable: retried


def test_a18b_t13_ip_class_cuts_to_the_network() -> None:
    assert ip_class("10.1.2.3") == "10.1.2.0/24"
    assert ip_class("2001:db8:1:2:3:4:5:6") == "2001:db8:1::/48"
    assert ip_class("not an address") is None and ip_class(None) is None


def test_a18b_t14_the_client_asks_for_the_security_event_types_only() -> None:
    from sbam_api.identity.keycloak_events import EVENT_TYPES

    assert "LOGIN_ERROR" in EVENT_TYPES and "REMOVE_TOTP" in EVENT_TYPES
    assert not {"LOGIN", "LOGOUT", "REFRESH_TOKEN"} & set(EVENT_TYPES)  # volume, not security
    assert isinstance(KeycloakEvents, type)
