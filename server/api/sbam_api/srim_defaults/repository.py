"""Tenant-scoped access to `tenant_srim_defaults` (E9.1b)."""

from sbam_contracts.manifest import ISA14_SRIM_DEFAULTS, SrimRuntimeDefaults

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.srim_defaults.models import TenantSrimDefaults


class SrimDefaultsRepository(TenantScopedRepository[TenantSrimDefaults]):
    model = TenantSrimDefaults

    def row(self, *, for_update: bool = False) -> TenantSrimDefaults | None:
        query = self._scoped()
        return self.session.scalars(query.with_for_update() if for_update else query).first()

    def current(self) -> tuple[SrimRuntimeDefaults, bool]:
        """The tenant's values and whether they are the ISA14 ones (no row yet)."""
        row = self.row()
        if row is None:
            return ISA14_SRIM_DEFAULTS, True
        return SrimRuntimeDefaults.model_validate(row.defaults), False
