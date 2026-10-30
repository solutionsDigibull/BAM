"""Authentication and the authorization chain (E2.2, TD §16, ADR 0002).

Token → tenant → portal role → project assignment → state guard. Every /api/v1 route declares a
policy dependency (`admin`, `fde`, `super_admin`, `internal`); `create_app` refuses to start
otherwise. Failures: 401 UNAUTHENTICATED (bad/missing token), 503 IDP_UNAVAILABLE (Keycloak down
and the token's key was never seen), 403 WRONG_PORTAL (wrong role), 404 NOT_FOUND (other tenant,
unassigned project, no tenant — identical to a missing resource), 423 via `locked()`.

The tables behind assignment and the audit log belong to later issues, so they sit behind ports
with fail-closed defaults: `MembershipLookup` (#50 project_members; default: no projects) and
`SecurityAudit` (#41 audit_log; default: a structured `sbam_api.security` log line).
`UserDirectory` (#49) mirrors the caller into `users` and refuses suspended or mismatched tenants;
`create_app` wires the SQL one, the default (tests) does nothing.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, Request
from sbam_contracts.api import ErrorCode

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.telemetry import set_request_context
from sbam_api.db.repository import TenantContext

ALLOWED_ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 30
JWKS_LIFESPAN_SECONDS = 600
UNKNOWN_KID_COOLDOWN_SECONDS = 30  # a forged kid can make us refetch at most this often
MIN_RSA_BITS = 2048
ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_FDE = "super-admin", "sbam-admin", "sbam-fde"
log = logging.getLogger("sbam_api.security")


def unauthenticated() -> ApiError:
    return ApiError(
        401,
        ErrorCode.UNAUTHENTICATED,
        "Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def idp_unavailable() -> ApiError:
    return ApiError(503, ErrorCode.IDP_UNAVAILABLE, "Identity provider unavailable; retry later")


def wrong_portal() -> ApiError:
    return ApiError(403, ErrorCode.WRONG_PORTAL, "This account cannot use this portal")


# --- Token verification ----------------------------------------------------------------------


class Jwks(jwt.PyJWKClient):
    """Keycloak's JWKS: cached 10 min, unknown-kid refetch at most every 30 s (PyJWT's cooldown).

    Also remembers the last good key set, so tokens signed with a known key keep verifying while
    Keycloak is down even after the cache lifespan ends.
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            url,
            lifespan=JWKS_LIFESPAN_SECONDS,
            cooldown_duration=UNKNOWN_KID_COOLDOWN_SECONDS,
            timeout=3,
        )
        self.last_keys: list[jwt.PyJWK] = []

    def get_signing_keys(self, refresh: bool = False) -> list[jwt.PyJWK]:
        keys = super().get_signing_keys(refresh)
        self.last_keys = keys
        return keys

    def key_for(self, token: str) -> jwt.PyJWK:
        try:
            return self.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError:
            kid = jwt.get_unverified_header(token).get("kid")
            known = self.match_kid(self.last_keys, kid) if kid else None
            if known is None:
                raise idp_unavailable() from None
            return known
        except (jwt.PyJWKClientError, jwt.PyJWTError):
            raise unauthenticated() from None


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    keycloak_sub: str
    tenant_id: uuid.UUID | None
    roles: frozenset[str]
    assigned_project_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    username: str | None = None
    email: str | None = None
    display_name: str | None = None
    session_id: str | None = None  # Keycloak `sid`: a new value means a new login

    def context(self, project_id: uuid.UUID | None = None) -> TenantContext:
        """For the tenant-scoped repository (#36). Only call on tenant routes."""
        if self.tenant_id is None:
            raise not_found()
        return TenantContext(self.tenant_id, project_id)


def _tenant(claims: dict[str, Any]) -> uuid.UUID | None:
    """Exactly one tenant UUID in the claim (org alias = tenant UUID, ADR 0002); else none."""
    orgs = claims.get("tenant_id")
    if not (isinstance(orgs, list) and len(orgs) == 1 and isinstance(orgs[0], str)):
        return None
    try:
        return uuid.UUID(orgs[0])
    except ValueError:
        return None


def _text(value: Any, limit: int = 254) -> str | None:
    """A short claim string (RFC 5321 caps an address at 254), else None."""
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks: Jwks) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks

    def verify(self, token: str) -> CurrentUser:
        """A user from a valid access token; raises 401/503 ApiErrors. Never logs the token."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise unauthenticated() from None
        if header.get("alg") not in ALLOWED_ALGORITHMS:  # none / HS* refused before any key lookup
            raise unauthenticated()
        key = self.jwks.key_for(token)
        if not isinstance(key.key, RSAPublicKey) or key.key.key_size < MIN_RSA_BITS:
            raise unauthenticated()
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            user_id = uuid.UUID(claims["sub"])  # Keycloak subjects are UUIDs
        except (jwt.PyJWTError, ValueError):
            raise unauthenticated() from None
        roles = claims.get("realm_access", {}).get("roles", [])
        return CurrentUser(
            user_id=user_id,
            keycloak_sub=claims["sub"],
            tenant_id=_tenant(claims),
            roles=frozenset(r for r in roles if isinstance(r, str)),
            username=_text(claims.get("preferred_username")),
            email=_text(claims.get("email")),
            display_name=_text(claims.get("name")) or _text(claims.get("preferred_username")),
            session_id=_text(claims.get("sid"), 64),
        )


# --- Ports -----------------------------------------------------------------------------------


class MembershipLookup(Protocol):
    def assigned_projects(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        """Project ids the user is assigned to, within the tenant (#50: project_members)."""
        ...


class NoMemberships:
    """No membership store (tests, no DB): nobody is assigned, so FDE project routes answer 404."""

    def assigned_projects(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        return frozenset()


class SecurityAudit(Protocol):
    def denied(self, *, actor: CurrentUser, route: str, target: str | None, reason: str) -> None:
        """Record a denied access (#41 writes it to audit_log)."""
        ...

    def event(
        self,
        *,
        actor: str,
        tenant_id: uuid.UUID | None,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        """Record an identity event: LOGIN, TENANT_CREATED, TENANT_STATUS_CHANGED (#41)."""
        ...


class LogSecurityAudit:
    """Until #41 lands: one structured warning per denial (actor = sub, never the token)."""

    def denied(self, *, actor: CurrentUser, route: str, target: str | None, reason: str) -> None:
        log.warning(
            "access denied",
            extra={"actor": actor.keycloak_sub, "route": route, "target": target, "reason": reason},
        )

    def event(
        self,
        *,
        actor: str,
        tenant_id: uuid.UUID | None,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        log.info(
            "audit",
            extra={
                "actor": actor,
                "action": action,
                "entity": f"{entity}:{entity_id}",
                "detail": {"tenant_id": str(tenant_id), "before": before, "after": after},
            },
        )


class UserDirectory(Protocol):
    def sync(self, user: CurrentUser) -> None:
        """Mirror a tenant user on each request; raise 403 for a suspended or mismatched tenant."""
        ...


class NoDirectory:
    def sync(self, user: CurrentUser) -> None:
        return None


class OrgDirectory(Protocol):
    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        """Create the tenant's Keycloak organization (alias = tenant id); its id. 503 on failure."""
        ...

    def delete(self, org_id: str) -> None:
        """Compensating delete when the tenant row can't be committed."""
        ...


class NoOrgs:
    """Keycloak admin credentials not configured: tenant creation answers 503."""

    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        raise idp_unavailable()

    def delete(self, org_id: str) -> None:
        return None


class UserAdmin(Protocol):
    """Keycloak users of a tenant (E2.4). Every method answers 503 IDP_UNAVAILABLE on failure."""

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        """Create the user in the tenant's organization and send the invite; their id. 409
        USER_EXISTS when the email is taken."""
        ...

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None: ...

    def set_enabled(self, user_id: str, enabled: bool) -> None: ...

    def logout(self, user_id: str) -> None:
        """Revoke all the user's sessions."""
        ...

    def delete(self, user_id: str) -> None:
        """Compensating delete when the users row can't be committed."""
        ...


class NoUserAdmin:
    """Keycloak admin credentials not configured: user changes answer 503."""

    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        raise idp_unavailable()

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None:
        raise idp_unavailable()

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        raise idp_unavailable()

    def logout(self, user_id: str) -> None:
        raise idp_unavailable()

    def delete(self, user_id: str) -> None:
        return None


@dataclass
class Auth:
    verifier: TokenVerifier
    memberships: MembershipLookup = field(default_factory=NoMemberships)
    audit: SecurityAudit = field(default_factory=LogSecurityAudit)
    directory: UserDirectory = field(default_factory=NoDirectory)
    orgs: OrgDirectory = field(default_factory=NoOrgs)
    users: UserAdmin = field(default_factory=NoUserAdmin)


# --- Dependencies ----------------------------------------------------------------------------


def current_user(request: Request) -> CurrentUser:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthenticated()
    auth: Auth = request.app.state.auth
    user = auth.verifier.verify(token.strip())
    # after verify: a forged token spends nobody's budget
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        limiter.check(str(user.user_id), request.method, request.url.path)
    if user.tenant_id is not None:
        assigned = auth.memberships.assigned_projects(user.tenant_id, user.user_id)
        user = CurrentUser(**{**user.__dict__, "assigned_project_ids": assigned})
        set_request_context(tenant_id=str(user.tenant_id))  # logs + span, only after verification
        auth.directory.sync(user)
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def admin(user: CurrentUserDep) -> CurrentUser:
    """Admin portal APIs (`/admin/...`, tenant-scoped): sbam-admin, or super-admin."""
    if not user.roles & {ROLE_ADMIN, ROLE_SUPER_ADMIN}:
        raise wrong_portal()
    if user.tenant_id is None:
        raise not_found()
    return user


def fde(user: CurrentUserDep) -> CurrentUser:
    """FDE portal APIs (`/projects/{project_id}/...`): sbam-fde in a tenant."""
    if ROLE_FDE not in user.roles:
        raise wrong_portal()
    if user.tenant_id is None:
        raise not_found()
    return user


def super_admin(user: CurrentUserDep) -> CurrentUser:
    """Platform APIs (`/admin/tenants`): super-admin, no tenant needed."""
    if ROLE_SUPER_ADMIN not in user.roles:
        raise wrong_portal()
    return user


def admin_or_fde(user: CurrentUserDep) -> CurrentUser:
    """Platform data both portals read (`/admin/filetypes`): sbam-admin, super-admin or sbam-fde.
    A signed-in account with none of those roles (and no tenant needed) is the wrong portal."""
    if not user.roles & {ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_FDE}:
        raise wrong_portal()
    return user


def authenticated(user: CurrentUserDep) -> CurrentUser:
    """Any signed-in user, whatever the portal (`/me`)."""
    return user


def internal() -> None:
    """The route authenticates the caller itself (signed URL, hook secret); no user token."""


POLICIES: frozenset[Callable[..., Any]] = frozenset(
    {admin, fde, super_admin, admin_or_fde, authenticated, internal}
)


def project_access(
    project_id: str, request: Request, user: Annotated[CurrentUser, Depends(fde)]
) -> uuid.UUID:
    """The FDE's assigned project from the path. Other tenant, unassigned, malformed id: 404."""
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise not_found() from None
    if pid.version != 7 or pid not in user.assigned_project_ids:
        if pid.version == 7:  # a real-looking id the caller may not see: worth an audit entry
            request.app.state.auth.audit.denied(
                actor=user, route=request.url.path, target=str(pid), reason="not assigned"
            )
        raise not_found()
    set_request_context(project_id=str(pid))
    return pid
