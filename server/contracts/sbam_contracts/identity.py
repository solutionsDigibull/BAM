"""Identity API contracts: tenants (super-admin) and the caller's own profile (TD §8.2, issue E2.3).

Rules worth knowing
  * A tenant name is trimmed, then must be 2..120 characters with no control characters. Names are
    unique across the platform ignoring case (the API answers 409 DUPLICATE_NAME).
  * A tenant is ACTIVE or SUSPENDED. Every request from a SUSPENDED tenant's users answers 403
    TENANT_SUSPENDED. Status changes need `If-Match`.
  * `keycloak_org_id` is the tenant's Keycloak organization (alias = tenant id). It is null only for
    tenants seeded before their organization was linked (local dev).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from sbam_contracts._jsonschema import contract_schema

TenantName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=2, max_length=120, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$"
    ),
]


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantCreate(_Request):
    name: TenantName


class TenantPatch(_Request):
    status: TenantStatus


class Tenant(BaseModel):
    id: UUID
    name: str
    keycloak_org_id: str | None
    status: TenantStatus
    created_at: datetime


class UserProfile(BaseModel):
    """The caller's row in the users mirror, refreshed from the token on login."""

    id: UUID
    keycloak_sub: str
    tenant_id: UUID
    email: str | None
    display_name: str | None
    portal_roles: list[str]
    last_login_at: datetime | None


class Me(BaseModel):
    """GET /me. `user` and `tenant` are null for a platform super-admin, who has no tenant."""

    user: UserProfile | None
    tenant: Tenant | None
    roles: list[str]
    assigned_projects: list[UUID]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/identity/`."""
    models: dict[str, type[BaseModel]] = {
        "tenant": Tenant,
        "tenant_create": TenantCreate,
        "tenant_patch": TenantPatch,
        "me": Me,
    }
    return {stem: contract_schema(model, f"identity/{stem}") for stem, model in models.items()}
