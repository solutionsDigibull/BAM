"""`tenant_srim_defaults` (E9.1b): one row per tenant that set its own SRIM runtime values.

A tenant without a row gets the SBAM defaults (`sbam_contracts.manifest.SBAM_SRIM_DEFAULTS`: the
Temporal router of ADR 0031 and the Bifrost gateway of ADR 0028), not the legacy ISA14 sample
values, so nothing is seeded and a new tenant needs no setup. `defaults` is a
`SrimRuntimeDefaults` as JSON, validated before it is written and again when it is read.
"""

from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, TenantOwned

Json = JSON().with_variant(JSONB(), "postgresql")


class TenantSrimDefaults(TenantOwned, Base):
    __tablename__ = "tenant_srim_defaults"
    __table_args__ = (UniqueConstraint("tenant_id"),)  # one row per tenant; a race fails here

    defaults: Mapped[dict[str, Any]] = mapped_column(Json)
