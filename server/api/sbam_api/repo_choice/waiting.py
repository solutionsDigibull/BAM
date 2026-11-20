"""Which builds wait for the FDE's repository answer, for the builds dashboard (#931).

The question is the audit row `repo_question_asked` that `choose_repo` wrote when the build began
to wait; it is still open while no `repo_decided` row came after it. No new table: the dashboard
and the question card read the same rows. The run mirror does not carry the build's wait stage, so
this is also where the dashboard learns that the stage is `wait_repo_choice`.
"""

import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sbam_contracts.build_dashboard import WaitingFor, WaitingKind
from sbam_contracts.repo_choice import (
    DECIDED,
    QUESTION_ASKED,
    REPO_REMINDER_EVERY,
    RepoQuestion,
)
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy.orm import Session

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill

ENTITY = "sub_skill"


def pending_row(rows: dict[str, AuditLog]) -> AuditLog | None:
    """The question row, when no decision came after it."""
    asked, decided = rows.get(QUESTION_ASKED), rows.get(DECIDED)
    if asked is None or asked.after is None:
        return None
    return asked if decided is None or decided.id < asked.id else None


def waiting_for(
    session: Session,
    ctx: TenantContext,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    subs: list[SubSkill],
    now: datetime | None = None,
) -> dict[uuid.UUID, WaitingFor]:
    """Per Sub-Skill that is BUILDING and has an open question: what it waits for."""
    now = now or datetime.now(UTC)
    found: dict[uuid.UUID, WaitingFor] = {}
    for sub in subs:
        if sub.state != SubSkillState.BUILDING:
            continue
        rows: dict[str, AuditLog] = {}
        for row in entity_history(session, ctx.tenant_id, ENTITY, str(sub.id)):
            if row.action in (QUESTION_ASKED, DECIDED):  # oldest first: the last of an action wins
                rows[row.action] = row
        asked = pending_row(rows)
        if asked is None:
            continue
        try:
            question = RepoQuestion.model_validate(asked.after, strict=False)
        except ValidationError:
            continue  # a row this version cannot read is not shown half-way
        since = question.asked_at
        sent = max(0, int((now - since) / REPO_REMINDER_EVERY))
        found[sub.id] = WaitingFor(
            kind=WaitingKind.REPO_QUESTION,
            since=since,
            question_kind=question.kind,
            reminders_sent=sent,
            next_reminder_at=since + REPO_REMINDER_EVERY * (sent + 1),
            link=f"/api/v1/projects/{project_id}/workflows/{workflow_id}/repo-questions",
        )
    return found
