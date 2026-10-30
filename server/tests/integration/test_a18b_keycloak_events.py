"""A-18b (#541) against a real Keycloak (the pinned image, a throwaway container): the events API
really returns what the importer reads, the service account needs `view-events` and nothing is
copied that a person typed. The audit rows go to SQLite (the import is database-agnostic); the
Compose stack is never touched. Skipped when Docker or the pinned image is not already local."""

import json
import re
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.keycloak_events import KeycloakEvents, import_keycloak_events
from sbam_api.identity.models import Tenant, User
from sqlalchemy import pool, select
from sqlalchemy.orm import Session, sessionmaker

from .test_a18_keycloak_hardening import KEYCLOAK, STRONG, Keycloak, available, start

pytestmark = pytest.mark.integration

TENANT = uuid.uuid4()
CLIENT = "sbam-api-admin"
ROLES = ["manage-realm", "manage-users", "view-users", "query-users", "view-events"]


def dev_realm() -> dict[str, Any]:
    text = (KEYCLOAK / "realm-bullm.json").read_text().replace("${WEB_PORT:3000}", "3000")
    realm: dict[str, Any] = json.loads(text.replace("http://localhost:3000", "https://x.example"))
    realm["sslRequired"] = "none"
    return realm


@pytest.fixture(scope="module")
def kc(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Keycloak]:
    if not available():
        pytest.skip("Docker or the pinned Keycloak image is not available locally")
    yield from start(tmp_path_factory.mktemp("kc"), dev_realm())


def grant(kc: Keycloak, secret: str, roles: list[str]) -> None:
    """Create sbam-api-admin like seed.sh does, with exactly these realm-management roles."""
    found = kc.admin("GET", f"/clients?clientId={CLIENT}").json()
    if found:
        cid = found[0]["id"]
        updated = kc.admin("PUT", f"/clients/{cid}", json={**found[0], "secret": secret})
        assert updated.status_code == 204, updated.text  # seed.sh re-sets the secret each run
    else:
        made = kc.admin(
            "POST",
            "/clients",
            json={
                "clientId": CLIENT,
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "secret": secret,
            },
        )
        assert made.status_code == 201, made.text
        cid = made.headers["Location"].rsplit("/", 1)[1]
    account = kc.admin("GET", f"/clients/{cid}/service-account-user").json()["id"]
    management = kc.admin("GET", "/clients?clientId=realm-management").json()[0]["id"]
    mapping = f"/users/{account}/role-mappings/clients/{management}"
    current = kc.admin("GET", mapping).json()
    if current:
        kc.admin("DELETE", mapping, json=current)
    wanted = [kc.admin("GET", f"/clients/{management}/roles/{r}").json() for r in roles]
    assert kc.admin("POST", mapping, json=wanted).status_code == 204


def database(kc_user_id: str) -> sessionmaker[Session]:
    engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    for table in (Tenant.__table__, User.__table__, AuditLog.__table__):
        table.create(engine)  # type: ignore[attr-defined]
    made = make_sessionmaker(engine)
    with made() as s:
        s.add(Tenant(id=TENANT, name="T"))
        s.flush()
        s.add(User(tenant_id=TENANT, keycloak_sub=kc_user_id, email="e@x.test"))
        s.commit()
    return made


def feed(kc: Keycloak, secret: str) -> KeycloakEvents:
    return KeycloakEvents(f"{kc.base}/realms/bullm", CLIENT, secret)


def audit(made: sessionmaker[Session]) -> list[AuditLog]:
    with made() as s:
        return list(s.scalars(select(AuditLog).order_by(AuditLog.at)))


def run(made: sessionmaker[Session], source: KeycloakEvents) -> Any:
    with made() as s:
        return import_keycloak_events(s, source, datetime.now(UTC), "c-int")


def test_a18b_t01_failed_logins_and_role_changes_reach_the_audit_log_without_what_was_typed(
    kc: Keycloak,
) -> None:
    secret = secrets.token_hex(16)
    grant(kc, secret, ROLES)
    uid = kc.user("watched-user", "sbam-fde")
    typed = "my-real-Passw0rd-typed-in-the-wrong-box"
    assert kc.sign_in("sbam-fde", "watched-user", typed) == "denied"
    role = kc.admin("GET", "/roles/sbam-admin").json()
    kc.admin("POST", f"/users/{uid}/role-mappings/realm", json=[role])
    made = database(uid)

    result = run(made, feed(kc, secret))
    rows = audit(made)
    actions = {r.action for r in rows}
    assert result.failed_sources == [] and result.written == len(rows) >= 2
    assert "KEYCLOAK_LOGIN_FAILED" in actions
    assert "KEYCLOAK_ADMIN_CREATE_REALM_ROLE_MAPPING" in actions
    failed = next(r for r in rows if r.action == "KEYCLOAK_LOGIN_FAILED")
    assert failed.tenant_id == TENANT and failed.actor_id == uid
    assert failed.after is not None and failed.after["error"] == "invalid_user_credentials"
    granted = [
        r
        for r in rows
        if r.action == "KEYCLOAK_ADMIN_CREATE_REALM_ROLE_MAPPING"
        and r.after is not None
        and r.after.get("roles") == ["sbam-admin"]
    ]
    assert len(granted) == 1 and granted[0].tenant_id == TENANT  # the tenant of the changed user
    dump = json.dumps([[r.actor_id, r.after, r.entity_id] for r in rows])
    assert typed not in dump and "watched-user" not in dump and "Tenant admin" not in dump
    assert not re.search(r"\b\d+\.\d+\.\d+\.[1-9]\d*\b", dump)  # only networks (x.y.z.0/24)
    assert "/24" in dump


def test_a18b_t02_a_second_import_of_the_same_window_writes_nothing(kc: Keycloak) -> None:
    secret = secrets.token_hex(16)
    grant(kc, secret, ROLES)
    uid = kc.user("repeat-user", "sbam-fde")
    kc.sign_in("sbam-fde", "repeat-user", "wrong-password-9")
    made = database(uid)
    first = run(made, feed(kc, secret))
    count = len(audit(made))
    second = run(made, feed(kc, secret))
    assert first.written > 0 and second.written == 0 and second.already >= first.written
    assert len(audit(made)) == count


def test_a18b_t03_the_service_account_needs_view_events(kc: Keycloak) -> None:
    secret = secrets.token_hex(16)
    grant(kc, secret, [r for r in ROLES if r != "view-events"])
    made = database(str(uuid.uuid4()))
    result = run(made, feed(kc, secret))
    assert sorted(result.failed_sources) == ["keycloak_admin_event", "keycloak_event"]
    assert audit(made) == []  # nothing guessed when Keycloak says no
    with pytest.raises(httpx.HTTPStatusError) as err:
        feed(kc, secret).events(0)
    assert err.value.response.status_code == 403


def test_a18b_t04_manage_realm_alone_does_not_read_events(kc: Keycloak) -> None:
    secret = secrets.token_hex(16)
    grant(kc, secret, ["manage-realm"])
    with pytest.raises(httpx.HTTPStatusError) as err:
        feed(kc, secret).admin_events(0)
    assert err.value.response.status_code == 403


def test_a18b_t05_with_the_role_back_the_import_works_again(kc: Keycloak) -> None:
    secret = secrets.token_hex(16)
    grant(kc, secret, ROLES)
    uid = kc.user("after-fix-user", "sbam-fde")
    kc.sign_in("sbam-fde", "after-fix-user", STRONG + "x")
    made = database(uid)
    assert run(made, feed(kc, secret)).failed_sources == []
    assert any(r.action == "KEYCLOAK_LOGIN_FAILED" for r in audit(made))
