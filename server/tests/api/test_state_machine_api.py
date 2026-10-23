"""E1.8 errors as HTTP problems: a handler calling the service gets 409/412/423/404 problem+json.

There is no entity API yet (each arrives with its feature), so a test-only route plays the handler:
it takes the caller's tenant (the auth chain does that for real routes) and the If-Match version.
"""

import uuid
from typing import Annotated

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from sbam_api.core.errors import install_error_handlers
from sbam_api.core.logging import correlation_id_var
from sbam_api.core.middleware import CorrelationIdMiddleware
from sbam_api.db.repository import TenantContext
from sbam_api.state_machine import TransitionCommand, transition
from sbam_contracts.state_machines import Actor, MachineName
from sqlalchemy.orm import Session, sessionmaker
from state_db import SQLITE_MODELS, add, audit_rows, current, sqlite_sessions

TENANT_A, TENANT_B, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def make_client(sessions: sessionmaker[Session]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(CorrelationIdMiddleware)

    @app.post("/t/{machine}/{entity_id}/{event}")
    def run(
        machine: MachineName,
        entity_id: uuid.UUID,
        event: str,
        x_tenant: Annotated[uuid.UUID, Header()],
        actor: Actor = Actor.FDE,
        if_match: Annotated[int | None, Header()] = None,
    ) -> dict[str, object]:
        result = transition(
            sessions,
            TenantContext(x_tenant, PROJECT),
            TransitionCommand(
                model=SQLITE_MODELS[machine],
                entity_id=entity_id,
                event=event,
                actor=actor,
                actor_id="user-1",
                correlation_id=correlation_id_var.get() or "none",
                expected_row_version=if_match,
            ),
        )
        return {"state": result.to_state, "row_version": result.row_version}

    return TestClient(app)


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return sqlite_sessions()


def post(
    client: TestClient,
    machine: MachineName,
    entity: uuid.UUID,
    event: str,
    tenant: uuid.UUID = TENANT_A,
    if_match: str | None = None,
) -> object:
    headers = {"x-tenant": str(tenant), "x-correlation-id": "cid-api"}
    if if_match is not None:
        headers["if-match"] = if_match
    return client.post(f"/t/{machine.value}/{entity}/{event}", headers=headers)


def problem(res: object, status: int, code: str) -> None:
    assert res.status_code == status, res.text  # type: ignore[attr-defined]
    assert res.headers["content-type"].startswith("application/problem+json")  # type: ignore[attr-defined]
    body = res.json()  # type: ignore[attr-defined]
    assert body["code"] == code and body["correlation_id"] == "cid-api"


def test_e1_8_ok_returns_new_state(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SQLITE_MODELS[MachineName.SOW_VERSION], "IN_REVIEW", TENANT_A, PROJECT)
    res = post(make_client(sessions), MachineName.SOW_VERSION, sow, "approve", if_match="1")
    assert res.status_code == 200 and res.json() == {"state": "APPROVED", "row_version": 2}  # type: ignore[attr-defined]
    assert [r.correlation_id for r in audit_rows(sessions, sow)] == ["cid-api"]


def test_e1_8_t02_invalid_transition_409(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SQLITE_MODELS[MachineName.SOW_VERSION], "GENERATED", TENANT_A, PROJECT)
    problem(
        # edit is IN_REVIEW only (GENERATED -approve-> is allowed since E7.1)
        post(make_client(sessions), MachineName.SOW_VERSION, sow, "edit"),
        409,
        "INVALID_TRANSITION",
    )
    assert audit_rows(sessions) == []


def test_e1_8_t03_stale_if_match_412(sessions: sessionmaker[Session]) -> None:
    model = SQLITE_MODELS[MachineName.SOW_VERSION]
    sow = add(sessions, model, "IN_REVIEW", TENANT_A, PROJECT)
    client = make_client(sessions)
    post(client, MachineName.SOW_VERSION, sow, "edit")
    post(client, MachineName.SOW_VERSION, sow, "edit")  # now row_version 3
    problem(
        post(client, MachineName.SOW_VERSION, sow, "approve", if_match="2"),
        412,
        "PRECONDITION_FAILED",
    )
    assert current(sessions, model, sow) == ("IN_REVIEW", 3)


def test_e1_8_t08_frozen_edit_423(sessions: sessionmaker[Session]) -> None:
    sub = add(sessions, SQLITE_MODELS[MachineName.SUB_SKILL], "FROZEN", TENANT_A, PROJECT)
    problem(post(make_client(sessions), MachineName.SUB_SKILL, sub, "edit"), 423, "RESOURCE_LOCKED")


def test_e1_8_t09_cross_tenant_404(sessions: sessionmaker[Session]) -> None:
    model = SQLITE_MODELS[MachineName.DOCUMENT_VERSION]
    doc = add(sessions, model, "UPLOADED", TENANT_B, PROJECT)
    res = post(make_client(sessions), MachineName.DOCUMENT_VERSION, doc, "delete", TENANT_A)
    problem(res, 404, "NOT_FOUND")
    assert current(sessions, model, doc) == ("UPLOADED", 1)
    assert audit_rows(sessions) == []
