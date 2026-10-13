"""Keycloak organizations for tenants, through the `sbam-api-admin` service account (E2.3).

A tenant's organization has alias = tenant id (ADR 0002: the `tenant_id` claim carries the alias)
and the attribute `sbam_tenant_id`. Any Keycloak failure (down, timeout, 5xx, refused) is 503
IDP_UNAVAILABLE, so the caller rolls back and nothing half-created remains.
"""

import logging
import uuid

import httpx

from sbam_api.core.auth import idp_unavailable

TIMEOUT_SECONDS = 5
log = logging.getLogger("sbam_api.identity")


class KeycloakOrgs:
    def __init__(self, realm_url: str, client_id: str, client_secret: str) -> None:
        self.token_url = f"{realm_url}/protocol/openid-connect/token"
        self.orgs_url = realm_url.replace("/realms/", "/admin/realms/", 1) + "/organizations"
        self.client_id = client_id
        self.client_secret = client_secret

    def _client(self) -> httpx.Client:
        # ponytail: a token per call; tenant creation is rare. Cache it if this gets hot.
        res = httpx.post(
            self.token_url,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=TIMEOUT_SECONDS,
        )
        res.raise_for_status()
        token = res.json()["access_token"]
        return httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_SECONDS)

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
        except httpx.HTTPError:
            # The tenant row is gone either way; an orphan org has no SBAM tenant, so it grants
            # nothing (its members get TENANT_MISMATCH).
            log.error("keycloak organization cleanup failed", extra={"target": org_id})
