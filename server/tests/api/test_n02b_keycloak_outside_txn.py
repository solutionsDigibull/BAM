"""N-02b (#609): a Keycloak call never runs while a DB transaction (and so a pooled connection) is
open, and the failure semantics stay: 503 IDP_UNAVAILABLE and nothing half-created."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import CurrentUser, idp_unavailable
from sbam_api.core.conventions import ApiError
from sbam_api.db.session import make_sessionmaker
from sbam_api.identity import service
from sbam_api.identity.models import Tenant, User
from sbam_contracts.identity import UserInvite
from sqlalchemy.orm import Session, sessionmaker
from test_identity import Audit, sqlite_engine

TENANT = uuid.uuid4()
ACTOR = CurrentUser(
    user_id=uuid.uuid4(),
    keycloak_sub="admin-sub",
    tenant_id=TENANT,
    roles=frozenset({"tenant-admin"}),
)


class Orgs:
    """A fake Keycloak organization directory that records whether a transaction was open."""

    def __init__(self, session: Session, down: bool = False) -> None:
        self.session, self.down = session, down
        self.open_during_call: list[bool] = []
        self.deleted: list[str] = []

    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        self.open_during_call.append(self.session.in_transaction())
        if self.down:
            raise idp_unavailable()
        return "org-1"

    def delete(self, org_id: str) -> None:
        self.deleted.append(org_id)


class Users:
    def __init__(self, session: Session, down: bool = False) -> None:
        self.session, self.down = session, down
        self.open_during_call: list[bool] = []
        self.deleted: list[str] = []

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        self.open_during_call.append(self.session.in_transaction())
        if self.down:
            raise idp_unavailable()
        return str(uuid.uuid4())

    def delete(self, user_id: str) -> None:
        self.deleted.append(user_id)

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None: ...

    def set_enabled(self, user_id: str, enabled: bool) -> None: ...


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = sqlite_engine()
    AuditLog.metadata.create_all(engine, tables=[AuditLog.__table__])  # type: ignore[list-item]
    factory = make_sessionmaker(engine)
    with factory() as s:
        s.add(Tenant(id=TENANT, name="Acme"))
        s.commit()
    yield factory


def invite_body() -> UserInvite:
    return UserInvite.model_validate({"email": "new@acme.com", "roles": ["sbam-fde"]})


def users_of(sessions: sessionmaker[Session]) -> list[User]:
    with sessions() as s:
        return list(s.query(User))


def test_n02b_creating_a_tenant_calls_keycloak_with_no_transaction_open(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        orgs = Orgs(session)
        service.create_tenant(session, orgs, Audit(), "root", "Racing Co")
    assert orgs.open_during_call == [False]


def test_n02b_inviting_a_user_calls_keycloak_with_no_transaction_open(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        users = Users(session)
        row = service.invite_user(session, users, ACTOR, invite_body())  # type: ignore[arg-type]
    assert users.open_during_call == [False]
    assert [u.id for u in users_of(sessions)] == [row.id]


def test_n02b_keycloak_down_on_create_is_a_503_and_leaves_no_tenant(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        orgs = Orgs(session, down=True)
        with pytest.raises(ApiError) as err:
            service.create_tenant(session, orgs, Audit(), "root", "Racing Co")
    assert err.value.status == 503
    with sessions() as s:
        assert {t.name for t in s.query(Tenant)} == {"Acme"}


def test_n02b_keycloak_down_on_invite_is_a_503_and_leaves_no_user(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        users = Users(session, down=True)
        with pytest.raises(ApiError) as err:
            service.invite_user(session, users, ACTOR, invite_body())  # type: ignore[arg-type]
    assert err.value.status == 503 and users.open_during_call == [False]
    assert users_of(sessions) == []


def test_n02b_a_row_that_cannot_be_saved_still_deletes_the_keycloak_user(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("the insert failed")

    with sessions() as session:
        users = Users(session)
        monkeypatch.setattr(session, "commit", boom)
        with pytest.raises(RuntimeError):
            service.invite_user(session, users, ACTOR, invite_body())  # type: ignore[arg-type]
    assert len(users.deleted) == 1  # no orphan account is left behind
