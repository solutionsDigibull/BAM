"""E10.4 notification service: recipients, templates, escaping, link safety, dedupe."""

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from notifications_db import ADMIN_ROLE, add_project, add_user, notification_sessions, rows
from sbam_api.audit.outbox import OutboxItem
from sbam_api.notifications import service
from sbam_api.notifications.service import Source, notify_project
from sbam_api.notifications.sink import KINDS, InAppSink
from sbam_contracts.notifications import BODY_MAX, TITLE_MAX, NotificationType
from sqlalchemy.orm import Session, sessionmaker

TENANT, OTHER_TENANT = uuid.uuid4(), uuid.uuid4()


def world() -> tuple[sessionmaker[Session], uuid.UUID, uuid.UUID, uuid.UUID]:
    sessions = notification_sessions()
    fde = add_user(sessions, TENANT)
    project = add_project(sessions, TENANT, members=[fde])
    return sessions, fde, project, uuid.uuid4()


def send(sessions: sessionmaker[Session], project: uuid.UUID, **kw: Any) -> int:
    with sessions() as session:
        n = notify_project(
            session,
            TENANT,
            project_id=project,
            source=kw.pop("source", Source("wf", str(uuid.uuid4()))),
            **kw,
        )
        session.commit()
        return n


@pytest.mark.parametrize("link", ["javascript:alert(1)", "https://evil", "//evil.example/x"])
def test_e10_4_t05_security_link_injection_is_stored_without_a_link_and_logged(
    link: str, caplog: pytest.LogCaptureFixture
) -> None:
    sessions, fde, project, _ = world()
    with caplog.at_level(logging.ERROR, logger="sbam_api.notifications"):
        assert send(sessions, project, type=NotificationType.SOW_READY, link=link) == 1
    [row] = rows(sessions, fde)
    assert row.link is None
    assert any(r.levelno == logging.ERROR and "link rejected" in r.message for r in caplog.records)
    assert link not in caplog.text  # untrusted value is not echoed into the log


@pytest.mark.parametrize(
    "link",
    [
        "/projects/../admin",
        "/projects/a//b",
        "/projects/a\\b",
        "/projects/a b",
        "/projects/x?next=https://evil",
        "/projects/x#frag",
        "projects/x",
        "/admin/users",
        "/projects/" + "a" * 400,
    ],
)
def test_e10_4_safe_link_rejects_everything_but_plain_project_paths(link: str) -> None:
    assert service.safe_link(link) is None


def test_e10_4_safe_link_accepts_the_app_routes() -> None:
    p, w = uuid.uuid4(), uuid.uuid4()
    for ntype in NotificationType:
        link = service.default_link(ntype, p, w)
        assert service.safe_link(link) == link and link.startswith(f"/projects/{p}")
    assert service.safe_link(None) is None


def test_e10_4_default_link_is_stored_and_points_into_the_project() -> None:
    sessions, fde, project, workflow = world()
    send(sessions, project, type=NotificationType.WORKFLOW_FROZEN, workflow_id=workflow)
    [row] = rows(sessions, fde)
    assert row.link == f"/projects/{project}/workflows/{workflow}/versions"
    assert row.workflow_id == workflow and row.project_id == project


def test_e10_4_untrusted_text_is_escaped_and_truncated_and_limits_hold() -> None:
    sessions, fde, project, _ = world()
    hostile = "<script>alert(1)</script>\x00\n" + "A" * 5000
    send(sessions, project, type=NotificationType.GENERATION_FAILED, params={"error": hostile})
    [row] = rows(sessions, fde)
    assert "<script>" not in row.body and "&lt;script&gt;" in row.body
    assert "\x00" not in row.body and "\n" not in row.body
    assert len(row.body) <= BODY_MAX and len(row.title) <= TITLE_MAX


def test_e10_4_a_hostile_project_name_cannot_overflow_the_title() -> None:
    title, body = service.render(
        NotificationType.STAGE_COMPLETED, {"stage": "&" * 500, "project": "<b>" * 500}
    )
    assert len(title) <= TITLE_MAX and len(body) <= BODY_MAX
    assert "<" not in title and "<" not in body


def test_e10_4_clip_never_cuts_an_html_entity_in_half() -> None:
    text = "a" * 8 + "&lt;" + "b" * 10
    for limit in range(8, 16):
        out = service.clip(text, limit)
        assert len(out) <= limit
        stripped = out.removesuffix("…")
        assert not stripped.endswith(("&", "&l", "&lt")) or stripped.endswith("&lt;")


def test_e10_4_recipients_are_assigned_active_fdes_in_the_same_tenant() -> None:
    sessions = notification_sessions()
    fde = add_user(sessions, TENANT)
    other_fde = add_user(sessions, TENANT)  # an FDE, but not assigned
    deactivated = add_user(sessions, TENANT, status="DEACTIVATED")
    admin_member = add_user(sessions, TENANT, roles=[ADMIN_ROLE])  # assigned, but not an FDE
    foreign = add_user(sessions, OTHER_TENANT)
    project = add_project(sessions, TENANT, members=[fde, deactivated, admin_member, foreign])
    assert send(sessions, project, type=NotificationType.SOW_READY) == 1
    assert [r.user_id for r in rows(sessions)] == [fde]
    assert other_fde not in {r.user_id for r in rows(sessions)}


def test_e10_4_admins_are_added_only_for_failures_and_get_no_link() -> None:
    sessions = notification_sessions()
    fde = add_user(sessions, TENANT)
    admin = add_user(sessions, TENANT, roles=[ADMIN_ROLE])
    project = add_project(sessions, TENANT, members=[fde])
    send(sessions, project, type=NotificationType.SOW_READY)
    assert {r.user_id for r in rows(sessions)} == {fde}
    send(sessions, project, type=NotificationType.GENERATION_FAILED)
    by_user = {r.user_id: r for r in rows(sessions) if r.type == "GENERATION_FAILED"}
    assert set(by_user) == {fde, admin}
    assert by_user[admin].link is None and by_user[fde].link is not None


def test_e10_4_other_tenants_project_notifies_nobody() -> None:
    sessions = notification_sessions()
    fde = add_user(sessions, OTHER_TENANT)
    project = add_project(sessions, OTHER_TENANT, members=[fde])
    assert send(sessions, project, type=NotificationType.SOW_READY) == 0
    assert rows(sessions) == []


def test_e10_4_same_source_twice_writes_once_per_user() -> None:
    sessions, fde, project, _ = world()
    source = Source("pipeline-x", "activity-7")
    assert send(sessions, project, type=NotificationType.SOW_READY, source=source) == 1
    assert send(sessions, project, type=NotificationType.SOW_READY, source=source) == 0
    assert len(rows(sessions, fde)) == 1


def test_e10_4_twenty_simultaneous_failures_make_twenty_notifications() -> None:
    sessions, fde, project, _ = world()
    for i in range(20):
        send(
            sessions,
            project,
            type=NotificationType.GENERATION_FAILED,
            params={"error": f"sub-skill {i} failed"},
        )
    assert len(rows(sessions, fde)) == 20  # no throttling, no merging


def test_e10_4_removed_member_keeps_old_notifications() -> None:
    sessions, fde, project, _ = world()
    send(sessions, project, type=NotificationType.SOW_READY)
    from sbam_api.projects.models import ProjectMember
    from sqlalchemy import delete

    with sessions() as session:
        session.execute(delete(ProjectMember))
        session.commit()
    assert send(sessions, project, type=NotificationType.SOW_READY) == 0  # no longer notified
    [row] = rows(sessions, fde)  # the old one is still there
    assert row.link == f"/projects/{project}/sow"  # opening it is the project API's 404


def test_e10_4_sink_maps_an_outbox_row_and_dedupes_a_second_delivery() -> None:
    sessions, fde, project, workflow = world()
    sink = InAppSink(sessions)
    item = OutboxItem(
        uuid.uuid4(),
        TENANT,
        "workflow.freeze",
        {"project_id": str(project), "entity_id": str(workflow), "to_state": "FROZEN"},
    )
    sink.send(item)
    sink.send(item)
    [row] = rows(sessions, fde)
    assert row.type == "WORKFLOW_FROZEN" and row.workflow_id == workflow
    assert (row.temporal_workflow_id, row.activity_id) == ("outbox", str(item.id))


def test_e10_4_sink_skips_events_the_user_does_not_hear_about() -> None:
    sessions, _, project, workflow = world()
    sink = InAppSink(sessions)
    payload = {"project_id": str(project), "entity_id": str(workflow)}
    for event in ("workflow.start_extraction", "pipeline.cancelled", "unknown.thing"):
        sink.send(OutboxItem(uuid.uuid4(), TENANT, event, payload))
    sink.send(OutboxItem(uuid.uuid4(), TENANT, "workflow.freeze", {"entity_id": str(workflow)}))
    assert rows(sessions) == []


def test_e10_4_every_notification_type_has_a_template_and_a_producer() -> None:
    assert set(service.TEMPLATES) == set(NotificationType)
    assert {k.type for k in KINDS.values()} == set(NotificationType)


def test_e10_4_notifications_are_in_app_only_no_email_sms_or_webhook_sender() -> None:
    root = Path(service.__file__).parent
    source = "\n".join(p.read_text() for p in root.glob("*.py")).lower()
    for banned in ("smtplib", "sendgrid", "twilio", "httpx", "requests", "aiosmtplib", "webhook"):
        assert banned not in source, banned
