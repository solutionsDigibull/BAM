"""Audit log read contract: `GET /admin/audit` (TD §8.2, issue E2.8).

Rules worth knowing
  * Admin only, and only the caller's tenant: platform rows (tenant creation) and other tenants'
    rows are never returned. The log is append-only; there is no write API.
  * Newest first, cursor pages (`Page[AuditEntry]`). Filters: `entity_type`, `entity_id`, `actor`,
    `from`/`to` (ISO 8601 with a timezone, at most 92 days apart). A bad query is 400.
  * `before`/`after` hold the changed fields only, never secrets or tokens.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from sbam_contracts._jsonschema import contract_schema


class AuditEntry(BaseModel):
    id: UUID
    action: str
    entity_type: str
    entity_id: str | None
    actor_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str
    created_at: datetime


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/audit/`."""
    return {"audit_entry": contract_schema(AuditEntry, "audit/audit_entry")}
