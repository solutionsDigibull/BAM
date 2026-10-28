"""SOW versions for the FDE (E6.3, TD §8.7): read, save an edit, compare.

* A version is immutable (DB trigger, migration 0020). A save writes a new version (`EDITED`,
  IN_REVIEW) whose parent is the version it was edited from; saving the same content again writes
  nothing and answers the current version.
* Concurrency: the ETag is the `sows` row's `row_version`, which moves with every new version. A
  save sends it as `If-Match` (428 without) plus `base_version`; either one stale is 409
  SOW_VERSION_CONFLICT naming the latest version. An APPROVED current version is 423 SOW_LOCKED
  (a scope change goes through P/sow/new-version, E7.x).
* The editor's TipTap document is converted to a `SowDocument` (`tiptap.py`): template sections
  and schema problems are 422 with a path. Every citation must name a chunk or entity of the
  version's OKF bundle, with its document version (no invented `okf_ref_id`). An FDE-authored
  citation (`kind: fde`) names no bundle id; the API signs it with the saving user and today.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.sow import AnyCitation, FdeCitation, SowDocument
from sbam_contracts.sow_api import (
    MAX_TIPTAP_BYTES,
    SECTION_TITLES,
    SectionChange,
    SowCitationRef,
    SowDiff,
    SowDiffItem,
    SowOverview,
    SowSaveRequest,
    SowSectionDiff,
    SowVersionDetail,
    SowVersionList,
    SowVersionSource,
    SowVersionStatus,
    SowVersionSummary,
    TipTapNode,
)
from sbam_contracts.state_machines import Actor, SowVersionState
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, etag, not_found
from sbam_api.core.ids import uuid7
from sbam_api.knowledge import graph
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.sow.models import Sow, SowSource, SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.sow.service import canonical_sha256
from sbam_api.sow.tiptap import TipTapInvalid, citations_of, from_tiptap, stamp_fde
from sbam_api.state_machine.service import apply_create
from sbam_api.storage.base import StorageBackend

SOW_EDITED = "SOW_EDITED"  # audit action of a save


def _sow(session: Session, user: CurrentUser, project_id: uuid.UUID, lock: bool = False) -> Sow:
    sow = SowRepository(session, user.context(project_id)).of_project(for_update=lock)
    if sow is None:
        raise not_found()
    return sow


def _numbers(session: Session, user: CurrentUser, project_id: uuid.UUID) -> dict[uuid.UUID, int]:
    sow = _sow(session, user, project_id)
    rows = SowVersionRepository(session, user.context(project_id)).of_sow(sow.id)
    return {r.id: r.version for r in rows}


def summary(row: SowVersion, numbers: dict[uuid.UUID, int]) -> SowVersionSummary:
    return SowVersionSummary(
        id=row.id,
        version=row.version,
        status=SowVersionStatus(row.state),
        source=SowVersionSource(row.source),
        sha256=row.sha256,
        parent_version=numbers.get(row.parent_version_id) if row.parent_version_id else None,
        knowledge_version_id=row.knowledge_version_id,
        error_reason=row.error_reason,
        author=row.created_by if row.source == SowSource.EDITED.value else None,
        created_at=row.created_at,
    )


def _version(session: Session, user: CurrentUser, project_id: uuid.UUID, number: int) -> SowVersion:
    sow = _sow(session, user, project_id)
    row = SowVersionRepository(session, user.context(project_id)).by_number(sow.id, number)
    if row is None:
        raise not_found()
    return row


# --- reads ----------------------------------------------------------------------------------------


def overview(session: Session, user: CurrentUser, project_id: uuid.UUID) -> SowOverview:
    sow = _sow(session, user, project_id)
    repo = SowVersionRepository(session, user.context(project_id))
    rows = {r.id: r for r in repo.of_sow(sow.id)}
    numbers = {i: r.version for i, r in rows.items()}

    def pick(version_id: uuid.UUID | None) -> SowVersionSummary | None:
        return summary(rows[version_id], numbers) if version_id in rows else None

    return SowOverview(
        sow_id=sow.id,
        current=pick(sow.current_version_id),
        approved=pick(sow.approved_version_id),
        etag=etag(sow.row_version),
    )


def list_versions(session: Session, user: CurrentUser, project_id: uuid.UUID) -> SowVersionList:
    sow = _sow(session, user, project_id)
    rows = SowVersionRepository(session, user.context(project_id)).of_sow(sow.id)
    numbers = {r.id: r.version for r in rows}
    return SowVersionList(items=[summary(r, numbers) for r in rows])


def _ref(path: str, c: AnyCitation) -> SowCitationRef:
    if isinstance(c, FdeCitation):
        return SowCitationRef(
            path=path, kind="fde", author=c.author, date=c.date.isoformat(), note=c.note
        )
    return SowCitationRef(
        path=path,
        okf_ref_id=c.okf_ref_id,
        document_version_id=c.document_version_id,
        location=c.location.model_dump(exclude_none=True),
    )


def detail(
    session: Session, user: CurrentUser, project_id: uuid.UUID, number: int
) -> tuple[SowVersionDetail, str]:
    """The version and the ETag a save from it must send."""
    row = _version(session, user, project_id, number)
    numbers = _numbers(session, user, project_id)
    cites: list[SowCitationRef] = []
    if row.structured is not None:
        doc = SowDocument.model_validate(row.structured)
        cites = [_ref(path, c) for path, c in citations_of(doc)]
    out = SowVersionDetail(
        **summary(row, numbers).model_dump(),
        content_tiptap=(
            TipTapNode.model_validate(row.content_tiptap) if row.content_tiptap else None
        ),
        structured=row.structured,
        citations=cites,
    )
    return out, etag(_sow(session, user, project_id).row_version)


# --- save -----------------------------------------------------------------------------------------


def _conflict(latest: int) -> ApiError:
    return ApiError(
        409,
        ErrorCode.SOW_VERSION_CONFLICT,
        "The SOW changed since you loaded it",
        f"The latest version is v{latest}; reload it and apply your edit again.",
        errors=[FieldError(path="base_version", message=f"latest is {latest}")],
    )


def _invalid(errors: list[FieldError]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, "Invalid SOW", errors=errors[:100])


def too_large() -> ApiError:
    return ApiError(
        413,
        ErrorCode.PAYLOAD_TOO_LARGE,
        "SOW too large",
        f"content_tiptap is limited to {MAX_TIPTAP_BYTES} bytes.",
    )


def tiptap_size(body: SowSaveRequest) -> int:
    """Bytes of `content_tiptap` as compact JSON: the measure of the 2 MB limit."""
    data = body.content_tiptap.model_dump(mode="json", exclude_none=True)
    return len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _check_citations(
    storage: StorageBackend,
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    doc: SowDocument,
    knowledge_version_id: uuid.UUID,
) -> None:
    kv = KnowledgeVersionRepository(session, user.context(project_id)).get(knowledge_version_id)
    known: dict[str, set[uuid.UUID]] = {}
    if kv is not None and kv.okf_bundle_key:
        loaded = graph.load(storage, kv)
        for chunk in loaded.chunks.values():
            known.setdefault(chunk.id, set()).add(chunk.document_version_id)
        for entity in loaded.entities:
            for ref in entity.source_refs:
                known.setdefault(entity.id, set()).add(ref.document_version_id)
    errors = [
        FieldError(path=f"{path}.citations", message="UNKNOWN_CITATION: not in the OKF bundle")
        for path, c in citations_of(doc)
        if not isinstance(c, FdeCitation)  # the FDE's own: nothing in the bundle to match
        and c.document_version_id not in known.get(c.okf_ref_id, set())
    ]
    if errors:
        raise _invalid(list({e.path: e for e in errors}.values()))


def _signer(user: CurrentUser) -> str:
    return (user.display_name or user.username or user.email or str(user.user_id))[:200]


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _fde_stamps(structured: dict[str, Any] | None) -> set[tuple[str, str, str | None]]:
    """(author, date, note) of the FDE-authored citations the saved version already holds."""
    if structured is None:
        return set()
    doc = SowDocument.model_validate(structured)
    return {
        (c.author, c.date.isoformat(), c.note)
        for _, c in citations_of(doc)
        if isinstance(c, FdeCitation)
    }


@dataclass(frozen=True)
class Saved:
    version: SowVersionSummary
    etag: str
    created: bool  # False: same content as the current version, nothing written


def save(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    body: SowSaveRequest,
    if_match: str | None,
    correlation_id: str,
) -> Saved:
    if tiptap_size(body) > MAX_TIPTAP_BYTES:
        raise too_large()
    ctx = user.context(project_id)
    sow = _sow(session, user, project_id, lock=True)
    versions = SowVersionRepository(session, ctx)
    current = versions.get(sow.current_version_id) if sow.current_version_id else None
    if current is None:
        raise not_found()
    if current.state in (SowVersionState.APPROVED.value, SowVersionState.SUPERSEDED.value):
        raise ApiError(
            423,
            ErrorCode.SOW_LOCKED,
            "The SOW is approved",
            "An approved SOW cannot be edited; start a new version for a scope change.",
        )
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    if (
        if_match.strip().removeprefix("W/") != etag(sow.row_version)
        or body.base_version != current.version
    ):
        raise _conflict(current.version)
    content = body.content_tiptap.model_dump(mode="json", exclude_none=True)
    stamp_fde(content, _fde_stamps(current.structured), _signer(user), _today())
    try:
        doc = from_tiptap(content)
    except TipTapInvalid as exc:
        raise _invalid(exc.errors) from None
    _check_citations(storage, session, user, project_id, doc, current.knowledge_version_id)
    structured = doc.model_dump(mode="json")
    sha = canonical_sha256(structured)
    numbers = {r.id: r.version for r in versions.of_sow(sow.id)}
    if sha == current.sha256:
        return Saved(summary(current, numbers), etag(sow.row_version), created=False)

    version = SowVersion(
        id=uuid7(),
        sow_id=sow.id,
        version=versions.next_number(sow.id),
        source=SowSource.EDITED.value,
        run_id=uuid7(),
        knowledge_version_id=current.knowledge_version_id,
        parent_version_id=current.id,
        structured=structured,
        content_tiptap=content,
        sha256=sha,
        created_by=str(user.user_id),
    )
    apply_create(
        session,
        ctx,
        version,
        event="new_version",
        actor=Actor.FDE,
        actor_id=str(user.user_id),
        correlation_id=correlation_id,
    )
    sow.current_version_id = version.id
    SowRepository(session, ctx).update(sow, sow.row_version)
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=SOW_EDITED,
        entity_type="sow_version",
        entity_id=str(version.id),
        correlation_id=correlation_id,
        before={"version": current.version, "sha256": current.sha256},
        after={"version": version.version, "sha256": sha},
    )
    session.commit()
    numbers[version.id] = version.version
    return Saved(summary(version, numbers), etag(sow.row_version), created=True)


# --- diff -----------------------------------------------------------------------------------------


def _change(old: Any, new: Any) -> SectionChange:
    if old is None and new is None:
        return SectionChange.UNCHANGED
    if old is None:
        return SectionChange.ADDED
    if new is None:
        return SectionChange.REMOVED
    return SectionChange.UNCHANGED if old == new else SectionChange.MODIFIED


def _items(old: Any, new: Any, key: str | None) -> list[SowDiffItem]:
    """Changed items: modules / features by id, other sections by field name."""
    if key is not None:
        a = {i[key]: i for i in old or []}
        b = {i[key]: i for i in new or []}
    else:
        a, b = dict(old or {}), dict(new or {})
    out = []
    for item_id in [*a, *(k for k in b if k not in a)]:
        change = _change(a.get(item_id), b.get(item_id))
        if change is not SectionChange.UNCHANGED:
            out.append(SowDiffItem(id=str(item_id), change=change))
    return out


def diff(
    session: Session, user: CurrentUser, project_id: uuid.UUID, number: int, other: int
) -> SowDiff:
    """What changed from version `number` to version `other`, section by section."""
    a = _version(session, user, project_id, number).structured or {}
    b = _version(session, user, project_id, other).structured or {}
    sections = []
    for section in SECTION_TITLES:
        old, new = a.get(section), b.get(section)
        key = "id" if section in ("modules", "features") else None
        items = _items(old, new, key)
        change = _change(old, new)
        sections.append(SowSectionDiff(section_id=section, change=change, items=items))  # type: ignore[arg-type]
    return SowDiff(version=number, other=other, sections=sections)
