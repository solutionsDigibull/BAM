"""E16.2 PostHog events from the outbox, on SQLite with a fake PostHog (no network)."""

import hashlib
import json
import uuid
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sbam_api.analytics import Analytics, AnalyticsEvent, PostHogClient, default_analytics
from sbam_api.analytics import posthog as posthog_module
from sbam_api.analytics.events import EVENTS
from sbam_api.audit import dispatch_pending
from sbam_api.audit.outbox import OutboxItem
from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import SettingsError, posthog_problem
from sbam_api.db.repository import TenantContext
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_contracts.state_machines import Actor, MachineName
from sqlalchemy.orm import Session, sessionmaker
from state_db import SQLITE_MODELS, add, outbox_rows, sqlite_sessions

SOW, SUB_SKILL, DOC = (
    SQLITE_MODELS[m]
    for m in (MachineName.SOW_VERSION, MachineName.SUB_SKILL, MachineName.DOCUMENT_VERSION)
)
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
CTX = TenantContext(TENANT, PROJECT)
FDE_ID = str(uuid.uuid4())
SALT = "pepper"


class FakePostHog:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[list[dict[str, Any]]] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("posthog down")
        assert url == "http://posthog:8000/batch/" and json["api_key"] == "phc_test"
        self.batches.append(json["batch"])
        return httpx.Response(200, request=httpx.Request("POST", url))


class Sink:
    def __init__(self) -> None:
        self.items: list[OutboxItem] = []

    def send(self, item: OutboxItem) -> None:
        self.items.append(item)


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return sqlite_sessions()


def analytics(fake: FakePostHog) -> Analytics:
    return Analytics(PostHogClient("http://posthog:8000", "phc_test", fake), "staging", SALT)


def apply(
    sessions: sessionmaker[Session], model: type[Any], entity: uuid.UUID, event: str, **kw: Any
) -> Any:
    actor = kw.pop("actor", Actor.SYSTEM)
    c = TransitionCommand(
        model=model,
        entity_id=entity,
        event=event,
        actor=actor,
        actor_id=FDE_ID if actor is Actor.FDE else SYSTEM_ACTOR_ID,
        correlation_id="cid-e16-2",
        **kw,
    )
    with sessions() as session:
        result = apply_transition(session, CTX, c)
        session.commit()  # the outbox row is visible only after this
    return result


def sent(fake: FakePostHog) -> list[dict[str, Any]]:
    return [event for batch in fake.batches for event in batch]


def test_e16_2_every_product_event_has_an_outbox_source() -> None:
    assert set(EVENTS.values()) == set(AnalyticsEvent)
    assert len(AnalyticsEvent) == 9


def test_e16_2_sow_approval_becomes_sow_approved(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SOW, "IN_REVIEW", TENANT, PROJECT)
    apply(sessions, SOW, sow, "approve", actor=Actor.FDE)
    fake = FakePostHog()
    assert dispatch_pending(sessions, Sink(), analytics=analytics(fake)) == 1
    [event] = sent(fake)
    assert event["event"] == "sow_approved"
    assert event["properties"]["stage"] == "APPROVED"
    assert event["properties"]["project_id"] == str(PROJECT)
    assert event["properties"]["environment"] == "staging"


def test_e16_2_t03_gate_failed_names_the_gate_then_a_correction_follows(
    sessions: sessionmaker[Session],
) -> None:
    sub = add(sessions, SUB_SKILL, "BUILDING", TENANT, PROJECT)
    apply(sessions, SUB_SKILL, sub, "gate_failed", details={"gate": "MYPY"})
    apply(sessions, SUB_SKILL, sub, "correction_applied")
    fake = FakePostHog()
    dispatch_pending(sessions, Sink(), analytics=analytics(fake))
    failed, corrected = sent(fake)
    assert failed["event"] == "gate_failed"
    assert failed["properties"]["gate"] == "MYPY"
    assert failed["properties"]["sub_skill_id"] == str(sub)
    assert corrected["event"] == "correction_attempted"
    assert corrected["properties"]["duration_ms"] >= 0  # time spent CORRECTING


def test_e16_2_t05_only_whitelisted_properties_and_a_salted_distinct_id(
    sessions: sessionmaker[Session],
) -> None:
    sub = add(sessions, SUB_SKILL, "BUILDING", TENANT, PROJECT)
    apply(
        sessions,
        SUB_SKILL,
        sub,
        "gate_failed",
        details={"gate": "RUFF", "message": "client text jane@example.com", "token": "s3cr3t"},
    )
    fake = FakePostHog()
    dispatch_pending(sessions, Sink(), analytics=analytics(fake))
    [event] = sent(fake)
    assert set(event["properties"]) == {
        "tenant_id",
        "project_id",
        "workflow_id",
        "subject_id",
        "sub_skill_id",
        "stage",
        "duration_ms",
        "environment",
        "gate",
    }
    dumped = json.dumps(event)
    assert "jane@example.com" not in dumped and "s3cr3t" not in dumped
    assert event["distinct_id"] == hashlib.sha256(f"{SALT}:{SYSTEM_ACTOR_ID}".encode()).hexdigest()


def test_e16_2_t05_a_person_is_a_hash_never_their_user_id(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SOW, "IN_REVIEW", TENANT, PROJECT)
    apply(sessions, SOW, sow, "approve", actor=Actor.FDE)
    fake = FakePostHog()
    dispatch_pending(sessions, Sink(), analytics=analytics(fake))
    [event] = sent(fake)
    assert FDE_ID not in json.dumps(event)
    assert event["distinct_id"] == hashlib.sha256(f"{SALT}:{FDE_ID}".encode()).hexdigest()


def test_e16_2_t06_posthog_down_never_blocks_the_dispatch(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    failures: list[int] = []
    monkeypatch.setattr(
        posthog_module, "FAILED", type("C", (), {"add": lambda _s, n: failures.append(n)})()
    )
    sow = add(sessions, SOW, "IN_REVIEW", TENANT, PROJECT)
    apply(sessions, SOW, sow, "approve", actor=Actor.FDE)
    sink = Sink()
    assert dispatch_pending(sessions, sink, analytics=analytics(FakePostHog(fail=True))) == 1
    assert [i.type for i in sink.items] == ["sow_version.approve"]  # the notification still went
    assert outbox_rows(sessions)[0].dispatched_at is not None
    assert failures == [1]


def test_e16_2_t07_a_retried_transition_is_one_event_and_a_resend_keeps_its_uuid(
    sessions: sessionmaker[Session],
) -> None:
    sub = add(sessions, SUB_SKILL, "TESTING", TENANT, PROJECT)
    apply(sessions, SUB_SKILL, sub, "tests_passed", idempotency_key="run-1:certify")
    apply(sessions, SUB_SKILL, sub, "tests_passed", idempotency_key="run-1:certify")  # the retry
    rows = outbox_rows(sessions)
    assert len(rows) == 1
    fake = FakePostHog()
    with sessions() as session:
        analytics(fake).send(session, rows)
        analytics(fake).send(session, rows)  # e.g. the sink failed and the pass ran again
    first, again = sent(fake)
    assert first["event"] == "sub_skill_certified"
    assert first["uuid"] == again["uuid"] == str(rows[0].id)  # PostHog keeps one


def test_e16_2_t08_a_rolled_back_transition_emits_nothing(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SOW, "IN_REVIEW", TENANT, PROJECT)
    with pytest.raises(ApiError):
        apply(sessions, SOW, sow, "approve", actor=Actor.FDE, expected_row_version=99)
    assert outbox_rows(sessions) == []
    fake = FakePostHog()
    assert dispatch_pending(sessions, Sink(), analytics=analytics(fake)) == 0
    assert fake.batches == []


def test_e16_2_rows_that_are_no_product_event_are_not_sent(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    apply(sessions, DOC, doc, "scan_started")
    fake = FakePostHog()
    assert dispatch_pending(sessions, Sink(), analytics=analytics(fake)) == 1  # notified as before
    assert fake.batches == []


def test_e16_2_without_posthog_configured_nothing_is_sent(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("POSTHOG_HOST", "POSTHOG_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    default_analytics.cache_clear()
    try:
        assert default_analytics().client is None
        sow = add(sessions, SOW, "IN_REVIEW", TENANT, PROJECT)
        apply(sessions, SOW, sow, "approve", actor=Actor.FDE)
        assert dispatch_pending(sessions, Sink()) == 1  # no-op analytics, notification unchanged
    finally:
        default_analytics.cache_clear()


def test_e16_2_posthog_outside_development_needs_a_salt() -> None:
    host, key = "http://posthog:8000", SecretStr("phc")
    with pytest.raises(SettingsError, match="POSTHOG_DISTINCT_ID_SALT"):
        posthog_problem("production", host, key, None)
    with pytest.raises(SettingsError):
        posthog_problem("staging", host, key, SecretStr(""))
    posthog_problem("production", host, key, SecretStr("pepper"))  # fine
    posthog_problem("development", host, key, None)  # local: allowed
    posthog_problem("production", None, None, None)  # PostHog off: nothing to check
    posthog_problem("production", "", SecretStr(""), None)  # what Compose passes when unset
