"""Create in-app notifications (E10.4, TD §8.13, §17, decision C-43: in-app only).

Everything a user reads here is built from our templates. Text that came from a model or a
document (an error message, a document name) is only ever a template *parameter*: control
characters stripped, cut to a short length, HTML-escaped, then the whole text is clipped to the
column limits. The link is a relative `/projects/...` path or nothing.
"""

import html
import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sbam_contracts.identity import UserStatus
from sbam_contracts.notifications import BODY_MAX, LINK_PREFIX, TITLE_MAX, NotificationType
from sqlalchemy.orm import Session

from sbam_api.core.auth import ROLE_ADMIN, ROLE_FDE
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.identity.models import User
from sbam_api.identity.repository import UserRepository
from sbam_api.notifications.models import LINK_MAX
from sbam_api.notifications.repository import NotificationRepository
from sbam_api.projects.repository import MemberRepository, ProjectRepository

log = logging.getLogger("sbam_api.notifications")

PARAM_MAX = 200  # raw characters of one template parameter, before escaping
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_PARTIAL_ENTITY = re.compile(r"&[#\w]*$")
_SAFE_LINK = re.compile(r"/projects/[A-Za-z0-9._~/\-]*")

# (title, body) per type. `{project}`, `{stage}`, `{error}`, `{next_action}` and `{waiting_for}` are
# filled from cleaned parameters; a missing parameter becomes an empty string.
TEMPLATES: dict[NotificationType, tuple[str, str]] = {
    NotificationType.SOW_READY: (
        "SOW ready for review",
        "The Statement of Work for {project} is ready for review.",
    ),
    NotificationType.BREAKDOWN_READY: (
        "Workflow breakdown ready",
        "The ISATVON breakdown for {project} is ready for review.",
    ),
    NotificationType.SUBSKILL_NEEDS_REVIEW: (
        "Sub-Skill needs review",
        "A Sub-Skill in {project} needs your review before it can be certified.",
    ),
    NotificationType.WORKFLOW_CERTIFIED: (
        "Workflow certified",
        "The workflow in {project} passed certification.",
    ),
    NotificationType.WORKFLOW_FROZEN: (
        "Workflow frozen",
        "The workflow in {project} is frozen. Create a new version to change it.",
    ),
    NotificationType.EXPORT_READY: (
        "Export ready",
        "An export for {project} is ready to download.",
    ),
    NotificationType.DOCUMENT_REJECTED: (
        "Document rejected",
        "A document in {project} was rejected and was not added to the knowledge base.",
    ),
    NotificationType.GENERATION_FAILED: (
        "Generation failed",
        "Generation for {project} failed at {stage}. {error} {next_action}",
    ),
    NotificationType.STAGE_COMPLETED: (
        "{stage} completed",
        "{stage} finished for {project}.",
    ),
    NotificationType.REMINDER: (
        "Reminder",
        "{project} is waiting for {waiting_for}.",
    ),
}
# Types an Admin also receives: failures they may have to act on. Admins have no FDE-portal
# access, so their copy carries no link.
ADMIN_TYPES = frozenset({NotificationType.GENERATION_FAILED})


def clean_param(value: object) -> str:
    """One template parameter: no control characters, bounded, HTML-escaped."""
    text = _CONTROL.sub(" ", str(value)).strip()
    return html.escape(text[:PARAM_MAX], quote=True)


def clip(text: str, limit: int) -> str:
    """At most `limit` characters; ends in an ellipsis when cut, never inside an HTML entity."""
    if len(text) <= limit:
        return text
    return _PARTIAL_ENTITY.sub("", text[: limit - 1]).rstrip() + "…"


def render(type: NotificationType, params: Mapping[str, object]) -> tuple[str, str]:
    title, body = TEMPLATES[type]
    cleaned = {key: clean_param(value) for key, value in params.items()}
    fill = _Fill(cleaned)
    return (
        clip(" ".join(title.format_map(fill).split()), TITLE_MAX),
        clip(" ".join(body.format_map(fill).split()), BODY_MAX),
    )


class _Fill(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def safe_link(link: str | None) -> str | None:
    """The link if it is a relative path under `/projects/`; otherwise None and an error log.

    Rejects other schemes (`javascript:`, `https:`), hosts and protocol-relative `//`, backslashes,
    `..` segments, whitespace/control characters and anything over the column length.
    """
    if link is None:
        return None
    ok = (
        len(link) <= LINK_MAX
        and link.startswith(LINK_PREFIX)
        and _SAFE_LINK.fullmatch(link) is not None
        and "//" not in link
        and ".." not in link.split("/")
    )
    if not ok:
        # Never log the value itself: it is untrusted and may be long.
        log.error("notification link rejected", extra={"link_length": len(link)})
        return None
    return link


def default_link(
    type: NotificationType, project_id: uuid.UUID, workflow_id: uuid.UUID | None
) -> str:
    base = f"{LINK_PREFIX}{project_id}"
    if type is NotificationType.SOW_READY:
        return f"{base}/sow"
    if type is NotificationType.DOCUMENT_REJECTED:
        return f"{base}/documents"
    if workflow_id is None:
        return base
    page = {
        NotificationType.BREAKDOWN_READY: "isatvon",
        NotificationType.SUBSKILL_NEEDS_REVIEW: "build",
        NotificationType.GENERATION_FAILED: "build",
        NotificationType.WORKFLOW_CERTIFIED: "certification",
        NotificationType.WORKFLOW_FROZEN: "versions",
        NotificationType.EXPORT_READY: "versions",
    }.get(type)
    return f"{base}/workflows/{workflow_id}" + (f"/{page}" if page else "")


def recipients(
    session: Session, ctx: TenantContext, project_id: uuid.UUID, type: NotificationType
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(FDEs, admins): active users of the tenant who are assigned to the project and hold
    `sbam-fde`; plus, for `ADMIN_TYPES`, the tenant's active `sbam-admin` users."""
    member_ids = MemberRepository(session, ctx).user_ids(project_id)
    users = UserRepository(session, ctx)
    # ponytail: roles are filtered in Python (as in UserRepository.other_active_admins); a tenant
    # has a handful of users, not thousands.
    fdes = [
        u.id
        for u in users.among(member_ids).values()
        if u.status == UserStatus.ACTIVE.value and ROLE_FDE in u.portal_roles
    ]
    admins: list[uuid.UUID] = []
    if type in ADMIN_TYPES:
        admins = [u.id for u in _active_admins(session, ctx)]
    return sorted(fdes), sorted(set(admins) - set(fdes))


def _active_admins(session: Session, ctx: TenantContext) -> Iterable[User]:
    rows = session.scalars(
        UserRepository(session, ctx)._scoped().where(User.status == UserStatus.ACTIVE.value)
    )
    return [u for u in rows if ROLE_ADMIN in u.portal_roles]


@dataclass(frozen=True)
class Source:
    """What produced the notification; with the user it is the dedupe key (a retry or a second
    delivery of the same outbox row writes nothing)."""

    workflow: str  # Temporal workflow id, or "outbox"
    activity: str  # Temporal activity id, or the outbox row id


def notify_project(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    type: NotificationType,
    project_id: uuid.UUID,
    source: Source,
    workflow_id: uuid.UUID | None = None,
    params: Mapping[str, object] | None = None,
    link: str | None = None,
) -> int:
    """One notification per recipient. Returns how many rows this call wrote (0 on a retry).

    The caller commits. No throttling: 20 failures notify 20 times.
    """
    ctx = TenantContext(tenant_id)
    project = ProjectRepository(session, ctx).get(project_id)
    if project is None:  # not this tenant's project: nobody to notify
        log.warning("notification for an unknown project skipped")
        return 0
    fdes, admins = recipients(session, ctx, project_id, type)
    if not fdes and not admins:
        return 0
    title, body = render(type, {"project": project.name, **(params or {})})
    chosen = safe_link(link if link is not None else default_link(type, project_id, workflow_id))
    repo = NotificationRepository(session, ctx)
    written = 0
    for user_id, user_link in [*((u, chosen) for u in fdes), *((u, None) for u in admins)]:
        written += repo.insert_if_new(
            {
                "id": uuid7(),
                "user_id": user_id,
                "project_id": project_id,
                "workflow_id": workflow_id,
                "type": type.value,
                "title": title,
                "body": body,
                "link": user_link,
                "temporal_workflow_id": source.workflow,
                "activity_id": source.activity,
            }
        )
    return written
