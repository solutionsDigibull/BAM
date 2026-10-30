"""Tenant-scoped queries for SOWs and their versions (E6.2)."""

import uuid
from datetime import datetime, timedelta

from sbam_contracts.state_machines import SowVersionState
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.sow.models import Sow, SowExport, SowImpactRecord, SowVersion
from sbam_api.state_machine.repository import StatefulRepository


class SowRepository(TenantScopedRepository[Sow]):
    """Use with a context that names the project: its one `sows` row."""

    model = Sow

    def of_project(self, *, for_update: bool = False) -> Sow | None:
        query = self._scoped()
        if for_update:
            query = query.with_for_update()
        found: Sow | None = self.session.scalars(query).one_or_none()
        return found


class SowVersionRepository(StatefulRepository):
    model = SowVersion

    def by_run(self, run_id: uuid.UUID, knowledge_version_id: uuid.UUID) -> SowVersion | None:
        query = self._scoped().where(
            SowVersion.run_id == run_id, SowVersion.knowledge_version_id == knowledge_version_id
        )
        found: SowVersion | None = self.session.scalars(query).one_or_none()
        return found

    def claim_delivery(self, version_id: uuid.UUID, now: datetime, stale: timedelta) -> bool:
        """Take the right to tell the pipeline about this approval: True for exactly one caller
        until it releases the claim or the claim goes stale. One atomic UPDATE (A-03)."""
        result = self.session.execute(
            update(SowVersion)
            .where(
                SowVersion.id == version_id,
                SowVersion.tenant_id == self.ctx.tenant_id,
                SowVersion.signal_sent_at.is_(None),
                or_(
                    SowVersion.signal_claimed_at.is_(None),
                    SowVersion.signal_claimed_at < now - stale,
                ),
            )
            .values(signal_claimed_at=now)
        )
        return bool(getattr(result, "rowcount", 0) == 1)

    def release_delivery(self, version_id: uuid.UUID) -> None:
        self.session.execute(
            update(SowVersion)
            .where(
                SowVersion.id == version_id,
                SowVersion.tenant_id == self.ctx.tenant_id,
                SowVersion.signal_sent_at.is_(None),
            )
            .values(signal_claimed_at=None)
        )

    def by_number(self, sow_id: uuid.UUID, version: int) -> SowVersion | None:
        query = self._scoped().where(SowVersion.sow_id == sow_id, SowVersion.version == version)
        found: SowVersion | None = self.session.scalars(query).one_or_none()
        return found

    def of_sow(self, sow_id: uuid.UUID) -> list[SowVersion]:
        """Newest first."""
        query = self._scoped().where(SowVersion.sow_id == sow_id)
        return list(self.session.scalars(query.order_by(SowVersion.version.desc())))

    def next_number(self, sow_id: uuid.UUID) -> int:
        query = (
            self._scoped(include_deleted=True)
            .where(SowVersion.sow_id == sow_id)
            .with_only_columns(func.max(SowVersion.version))
        )
        return int(self.session.scalar(query) or 0) + 1


class SowExportRepository(TenantScopedRepository[SowExport]):
    model = SowExport

    def find(
        self, sow_version_id: uuid.UUID, status: str, fmt: str, template_version: str
    ) -> SowExport | None:
        query = self._scoped().where(
            SowExport.sow_version_id == sow_version_id,
            SowExport.status == status,
            SowExport.format == fmt,
            SowExport.template_version == template_version,
        )
        found: SowExport | None = self.session.scalars(query).one_or_none()
        return found


class SowImpactRepository(TenantScopedRepository[SowImpactRecord]):
    """The impact stored at the approval of a scope-change version (E7.2)."""

    model = SowImpactRecord

    def of_version(self, sow_version_id: uuid.UUID) -> SowImpactRecord | None:
        query = self._scoped().where(SowImpactRecord.sow_version_id == sow_version_id)
        found: SowImpactRecord | None = self.session.scalars(query).one_or_none()
        return found


RELAY_BATCH = 50


def pending_approvals(session: Session, cutoff: datetime) -> list[SowVersion]:
    """System job (A-03): every approved version, in every tenant, whose pipeline was never told,
    oldest first. Read-only; never reachable from a request."""
    query = (
        select(SowVersion)
        .where(
            SowVersion.approved_at.is_not(None),
            SowVersion.signal_sent_at.is_(None),
            SowVersion.state == SowVersionState.APPROVED.value,
            SowVersion.approved_at <= cutoff,
        )
        .order_by(SowVersion.approved_at)
        .limit(RELAY_BATCH)
    )
    return list(session.scalars(query))
