"""The parsed master documents a project's knowledge may use (E3.8b).

A master document is context an Admin attached for one master, tenant-wide or for one project.
It reaches a project's snapshot, and so Cognee, only when it was scanned CLEAN and parsed
(`parse_status = PARSED`, `derived_md_key` set); a pending, failed, infected, deleted or other
tenant's document never does. Both the snapshot (`service.create_snapshot`) and the Cognee adapter's
scope check read this module, so they cannot disagree.
"""

import uuid
from collections.abc import Iterable

from sbam_contracts.documents import ScanStatus
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import ParseStatus, SnapshotDocument
from sqlalchemy import or_
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantContext, TenantScopedRepository
from sbam_api.masters.models import MasterDocument


class PinnableDocumentRepository(TenantScopedRepository[MasterDocument]):
    model = MasterDocument

    def parsed_for_project(self, project_id: uuid.UUID) -> list[MasterDocument]:
        """CLEAN and PARSED documents in the project's scope (the tenant's own and the project's),
        oldest first."""
        query = self._scoped().where(
            MasterDocument.scan_status == ScanStatus.CLEAN.value,
            MasterDocument.parse_status == ParseStatus.PARSED.value,
            MasterDocument.derived_md_key.is_not(None),
            or_(MasterDocument.project_id.is_(None), MasterDocument.project_id == project_id),
        )
        return list(self.session.scalars(query.order_by(MasterDocument.id)))


def pinned_documents(
    session: Session, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> list[SnapshotDocument]:
    """What a snapshot taken now would pin of the parsed master documents."""
    rows = PinnableDocumentRepository(session, TenantContext(tenant_id)).parsed_for_project(
        project_id
    )
    return [
        SnapshotDocument(
            document_id=row.id,
            title=row.title,
            master_type=MasterType(row.master_type),
            intent_class=row.intent_class,
            sha256=row.sha256,
            derived_md_key=row.derived_md_key or "",
        )
        for row in rows
    ]


def eligible_ids(
    session: Session, tenant_id: uuid.UUID, project_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """The ids among `ids` that are master documents the project may use (see the module doc)."""
    wanted = set(ids)
    rows = PinnableDocumentRepository(session, TenantContext(tenant_id)).parsed_for_project(
        project_id
    )
    return {row.id for row in rows} & wanted
