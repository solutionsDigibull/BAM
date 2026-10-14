"""Identity API contracts: tenants (super-admin) and the caller's own profile (TD §8.2, issue E2.3).

Rules worth knowing
  * A tenant name is trimmed, then must be 2..120 characters with no control characters. Names are
    unique across the platform ignoring case (the API answers 409 DUPLICATE_NAME).
  * A tenant is ACTIVE or SUSPENDED. Every request from a SUSPENDED tenant's users answers 403
    TENANT_SUSPENDED. Status changes need `If-Match`.
  * `keycloak_org_id` is the tenant's Keycloak organization (alias = tenant id). It is null only for
    tenants seeded before their organization was linked (local dev).
  * A tenant admin invites and edits users of their own tenant only (another tenant's user: 404).
    They may assign `sbam-admin` and `sbam-fde`, never `super-admin` (422 on `roles`). An email
    is unique across the platform (409 USER_EXISTS). A tenant always keeps one ACTIVE sbam-admin:
    a change that would remove the last one answers 409 LAST_ADMIN. Edits need `If-Match`.
  * A user is INVITED until their first login, then ACTIVE; DEACTIVATED users can't sign in.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from sbam_contracts._jsonschema import contract_schema

TenantName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=2, max_length=120, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$"
    ),
]


AssignableRole = Literal["sbam-admin", "sbam-fde"]
ASSIGNABLE_ROLES: frozenset[str] = frozenset({"sbam-admin", "sbam-fde"})

# ponytail: a plain shape check, not full RFC 5322; Keycloak and the invite email do the rest.
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    ),
]


def _check_roles(value: Any) -> Any:
    """One error on `roles` (not `roles.0`): unknown or super-admin, duplicates, empty."""
    if isinstance(value, list):
        if not value:
            raise ValueError("at least one role is required")
        if not set(value) <= ASSIGNABLE_ROLES:
            raise ValueError("roles must be sbam-admin and/or sbam-fde")
        if len(set(value)) != len(value):
            raise ValueError("roles must be unique")
    return value


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantCreate(_Request):
    name: TenantName


class TenantPatch(_Request):
    status: TenantStatus


class UserStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    DEACTIVATED = "DEACTIVATED"


class UserInvite(_Request):
    email: Email
    roles: list[AssignableRole]

    _roles = field_validator("roles", mode="before")(_check_roles)


class UserPatch(_Request):
    """Partial update: `roles` replaces the user's portal roles; `active=false` deactivates."""

    roles: list[AssignableRole] | None = None
    active: bool | None = None

    _roles = field_validator("roles", mode="before")(_check_roles)

    @model_validator(mode="after")
    def _something(self) -> "UserPatch":
        if self.roles is None and self.active is None:
            raise ValueError("send roles and/or active")
        return self


class TenantUser(BaseModel):
    """A user of the caller's tenant, as the admin sees it. The ETag header carries row_version."""

    id: UUID
    email: str | None
    display_name: str | None
    portal_roles: list[str]
    status: UserStatus
    last_login_at: datetime | None
    created_at: datetime
    row_version: int


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
        "user_invite": UserInvite,
        "user_patch": UserPatch,
        "tenant_user": TenantUser,
    }
    return {stem: contract_schema(model, f"identity/{stem}") for stem, model in models.items()}
