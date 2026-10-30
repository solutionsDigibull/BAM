"""Keycloak organizations (E2.3) and users (E2.4), through the `sbam-api-admin` service account.

A tenant's organization has alias = tenant id (ADR 0002: the `tenant_id` claim carries the alias)
and the attribute `sbam_tenant_id`. Any Keycloak failure (down, timeout, 5xx, refused) is 503
IDP_UNAVAILABLE, so the caller rolls back and nothing half-created remains.

An invited user is created enabled with an unverified email, gets the realm roles, joins the
tenant's organization (so their token carries `tenant_id`) and is sent the execute-actions email
(set a password, verify the address). If any step after the create fails, the user is deleted.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import httpx
from sbam_contracts.api import ErrorCode

from sbam_api.core.auth import idp_unavailable
from sbam_api.core.bulkhead import KEYCLOAK_ADMIN, BulkheadFullError
from sbam_api.core.conventions import ApiError

TIMEOUT_SECONDS = 5
log = logging.getLogger("sbam_api.identity")


INVITE_ACTIONS = ["UPDATE_PASSWORD", "VERIFY_EMAIL"]


def user_exists() -> ApiError:
    return ApiError(409, ErrorCode.USER_EXISTS, "A user with this email already exists")


class _AdminApi:
    def __init__(self, realm_url: str, client_id: str, client_secret: str) -> None:
        self.token_url = f"{realm_url}/protocol/openid-connect/token"
        self.admin_url = realm_url.replace("/realms/", "/admin/realms/", 1)
        self.orgs_url = f"{self.admin_url}/organizations"
        self.client_id = client_id
        self.client_secret = client_secret

    @contextmanager
    def _client(self) -> Iterator[httpx.Client]:
        """A client for one admin operation. It holds a Keycloak bulkhead slot for the whole
        operation, so a hung Keycloak cannot take the API's thread pool; when no slot is free it
        is 503 IDP_UNAVAILABLE at once."""
        try:
            with KEYCLOAK_ADMIN.slot():
                # ponytail: a token per call; tenant creation is rare. Cache it if this gets hot.
                res = httpx.post(
                    self.token_url,
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.client_secret),
                    timeout=TIMEOUT_SECONDS,
                )
                res.raise_for_status()
                token = res.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
                    yield client
        except BulkheadFullError:
            raise idp_unavailable() from None


class KeycloakOrgs(_AdminApi):
    def create(self, tenant_id: uuid.UUID, name: str) -> str:
        body = {
            "name": name,
            "alias": str(tenant_id),
            "enabled": True,
            "attributes": {"sbam_tenant_id": [str(tenant_id)]},
        }
        try:
            with self._client() as client:
                res = client.post(self.orgs_url, json=body)
                res.raise_for_status()
                # 201 with Location: .../organizations/<id>
                return res.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("keycloak organization create failed", extra={"reason": type(exc).__name__})
            raise idp_unavailable() from None

    def delete(self, org_id: str) -> None:
        try:
            with self._client() as client:
                client.delete(f"{self.orgs_url}/{org_id}").raise_for_status()
        except (httpx.HTTPError, ApiError):
            # The tenant row is gone either way; an orphan org has no SBAM tenant, so it grants
            # nothing (its members get TENANT_MISMATCH).
            log.error("keycloak organization cleanup failed", extra={"target": org_id})


class KeycloakUsers(_AdminApi):
    def invite(self, tenant_id: uuid.UUID, org_id: str | None, email: str, roles: list[str]) -> str:
        """Create and invite the user; their Keycloak id. 409 USER_EXISTS, 503 otherwise."""
        user_id: str | None = None
        try:
            with self._client() as client:
                res = client.post(
                    f"{self.admin_url}/users",
                    json={"username": email, "email": email, "enabled": True},
                )
                if res.status_code == 409:
                    raise user_exists()
                res.raise_for_status()
                user_id = res.headers["location"].rstrip("/").rsplit("/", 1)[-1]
                self._add_roles(client, user_id, roles)
                org = org_id or self._org_by_alias(client, str(tenant_id))
                client.post(f"{self.orgs_url}/{org}/members", json=user_id).raise_for_status()
                client.put(
                    f"{self.admin_url}/users/{user_id}/execute-actions-email", json=INVITE_ACTIONS
                ).raise_for_status()
                return user_id
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("keycloak invite failed", extra={"reason": type(exc).__name__})
            if user_id is not None:
                self.delete(user_id)
            raise idp_unavailable() from None

    def set_roles(self, user_id: str, before: list[str], after: list[str]) -> None:
        def call() -> None:
            with self._client() as client:
                self._add_roles(client, user_id, sorted(set(after) - set(before)))
                gone = sorted(set(before) - set(after))
                if gone:
                    client.request(
                        "DELETE", self._mappings(user_id), json=self._roles(client, gone)
                    ).raise_for_status()

        self._call("role change", call)

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        def call() -> None:
            with self._client() as client:
                url = f"{self.admin_url}/users/{user_id}"
                client.put(url, json={"enabled": enabled}).raise_for_status()

        self._call("enable", call)

    def logout(self, user_id: str) -> None:
        """Revoke every session (and so every refresh token) of the user."""

        def call() -> None:
            with self._client() as client:
                client.post(f"{self.admin_url}/users/{user_id}/logout").raise_for_status()

        self._call("logout", call)

    def delete(self, user_id: str) -> None:
        try:
            with self._client() as client:
                client.delete(f"{self.admin_url}/users/{user_id}").raise_for_status()
        except (httpx.HTTPError, ApiError):
            log.error("keycloak user cleanup failed", extra={"target": user_id})

    def _mappings(self, user_id: str) -> str:
        return f"{self.admin_url}/users/{user_id}/role-mappings/realm"

    def _roles(self, client: httpx.Client, names: list[str]) -> list[dict[str, object]]:
        reps = []
        for name in names:
            res = client.get(f"{self.admin_url}/roles/{name}")
            res.raise_for_status()
            reps.append(res.json())
        return reps

    def _add_roles(self, client: httpx.Client, user_id: str, names: list[str]) -> None:
        if names:
            client.post(self._mappings(user_id), json=self._roles(client, names)).raise_for_status()

    def _org_by_alias(self, client: httpx.Client, alias: str) -> str:
        # ponytail: the org list has no alias filter; a scan of 1000 covers dev-seeded tenants
        # (created before keycloak_org_id existed). API-created tenants pass their org id.
        res = client.get(self.orgs_url, params={"max": 1000, "briefRepresentation": "true"})
        res.raise_for_status()
        for org in res.json():
            if org.get("alias") == alias:
                return str(org["id"])
        raise ValueError("no organization for the tenant")

    @staticmethod
    def _call(what: str, call: Callable[[], None]) -> None:
        try:
            call()
        except httpx.HTTPError as exc:
            log.warning(f"keycloak user {what} failed", extra={"reason": type(exc).__name__})
            raise idp_unavailable() from None
