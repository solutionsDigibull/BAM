"""The in-app `NotificationSink` (E10.4): turns outbox rows into `notifications` rows.

`dispatch_pending` hands over each pending outbox row after its transaction committed. The row's
`type` is `<machine>.<event>` for a state transition, or a pipeline notify type (`pipeline.failed`,
`pipeline.reminder`). `KINDS` says which of those the user hears about; the rest (a stage starting,
a cancel the user asked for) are skipped. The outbox row id is the dedupe identity, so a row that
is sent twice writes no second notification.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sbam_contracts.notifications import NotificationType as T
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.audit.outbox import OutboxItem
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.notifications import service

log = logging.getLogger("sbam_api.notifications")


@dataclass(frozen=True)
class Kind:
    type: T
    stage: str = ""  # the "{stage}" template parameter


KINDS: dict[str, Kind] = {
    "project_pipeline.ingestion_completed": Kind(T.STAGE_COMPLETED, "Document ingestion"),
    "project_pipeline.knowledge_ready": Kind(T.STAGE_COMPLETED, "Knowledge base"),
    "project_pipeline.sow_generated": Kind(T.SOW_READY),
    "project_pipeline.workflow_created": Kind(T.STAGE_COMPLETED, "Workflow creation"),
    "project_pipeline.ingestion_failed": Kind(T.GENERATION_FAILED, "document ingestion"),
    "project_pipeline.knowledge_failed": Kind(T.GENERATION_FAILED, "knowledge extraction"),
    "project_pipeline.sow_generation_failed": Kind(T.GENERATION_FAILED, "SOW generation"),
    "workflow.extraction_completed": Kind(T.BREAKDOWN_READY),
    "workflow.extraction_failed": Kind(T.GENERATION_FAILED, "ISATVON extraction"),
    "workflow.sub_skill_needs_review": Kind(T.SUBSKILL_NEEDS_REVIEW),
    "workflow.all_sub_skills_certified": Kind(T.STAGE_COMPLETED, "Sub-Skill certification"),
    "workflow.workflow_certified": Kind(T.WORKFLOW_CERTIFIED),
    "workflow.workflow_validation_failed": Kind(T.GENERATION_FAILED, "workflow validation"),
    "workflow.freeze": Kind(T.WORKFLOW_FROZEN),
    "sub_skill.attempts_exhausted": Kind(T.SUBSKILL_NEEDS_REVIEW),
    # E9.3: a library the runtime lacks, still after the corrections; the guidance is on the
    # LIBRARIES gate finding.
    "sub_skill.dependency_failed": Kind(T.SUBSKILL_NEEDS_REVIEW),
    # #483: the model could not answer in a build stage; the Sub-Skill waits in NEEDS_FDE_REVIEW for
    # `retry`. The `error` parameter is the cause, `stage` the stage. FDEs and Admins both see it.
    "sub_skill.generation_error": Kind(T.GENERATION_FAILED, "body generation"),
    # #446: a correction changed what the Sub-Skill means, so it is held for the FDE instead of
    # certifying. The transition (`sub_skill.isatvon_review_required`) is deliberately not mapped.
    "sub_skill.isatvon_review": Kind(T.SUBSKILL_NEEDS_REVIEW),
    "document_version.malware_detected": Kind(T.DOCUMENT_REJECTED),
    "document_version.type_rejected": Kind(T.DOCUMENT_REJECTED),  # E4.4; the reason is on screen
    "document_version.parse_failed": Kind(T.DOCUMENT_REJECTED),
    "pipeline.failed": Kind(T.GENERATION_FAILED),
    "knowledge.rebuilt": Kind(T.STAGE_COMPLETED, "Knowledge rebuild"),  # E5.2, KnowledgeRebuild
    "knowledge.rebuild_failed": Kind(T.GENERATION_FAILED, "knowledge rebuild"),
    "sow.regenerated": Kind(T.SOW_READY),  # E6.3, SowRegenerate
    "sow.regeneration_failed": Kind(T.GENERATION_FAILED, "SOW regeneration"),
    "sow_version.approve": Kind(T.STAGE_COMPLETED, "SOW approval"),  # E7.1
    "pipeline.reminder": Kind(T.REMINDER),
}
for _type in T:  # a producer may also notify with a NotificationType value directly
    KINDS.setdefault(_type.value, Kind(_type))


# Why a document was rejected, in the words of the notification (the Documents screen has its own).
REJECT_REASONS: dict[str, str] = {
    "MALWARE": "The malware scan flagged it, so it was quarantined.",
    "TYPE_MISMATCH": "The file type does not match its extension.",
    "UNSUPPORTED_TYPE": "This file type is not supported.",
    "INVALID_ENCODING": "The text is not valid UTF-8.",
}
REJECTIONS = frozenset({"document_version.malware_detected", "document_version.type_rejected"})


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class InAppSink:
    """Writes the notifications for one outbox row in one transaction. An error propagates, so
    `dispatch_pending` leaves the row pending and retries it (the insert is idempotent)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def send(self, item: OutboxItem) -> None:
        kind = KINDS.get(item.type)
        if kind is None:
            return
        project_id = _uuid(item.payload.get("project_id"))
        if project_id is None:
            log.warning("outbox row %s has no project id; not notified", item.id)
            return
        entity_id = _uuid(item.payload.get("entity_id"))
        workflow_id = entity_id if item.type.startswith("workflow.") else None
        params: dict[str, object] = {
            "stage": item.payload.get("stage") or kind.stage,
            "error": item.payload.get("error", ""),
            "next_action": item.payload.get("next_action", ""),
            "waiting_for": item.payload.get("waiting_for", ""),
        }
        with self.sessions() as session:
            if item.type in REJECTIONS and entity_id is not None and not params["error"]:
                params["error"] = self._reject_reason(session, item, project_id, entity_id)
            service.notify_project(
                session,
                item.tenant_id,
                type=kind.type,
                project_id=project_id,
                workflow_id=workflow_id,
                params=params,
                source=service.Source("outbox", str(item.id)),
            )
            session.commit()

    @staticmethod
    def _reject_reason(
        session: Session, item: OutboxItem, project_id: uuid.UUID, version_id: uuid.UUID
    ) -> str:
        """The reason on the rejected version (`reject_code`); empty if it has none. Scoped to the
        row's tenant AND project, like every other repository call (E15.6)."""
        ctx = TenantContext(item.tenant_id, project_id)
        version = DocumentVersionRepository(session, ctx).get(version_id)
        return REJECT_REASONS.get(version.reject_code or "", "") if version is not None else ""
