"""The API's own docs (Swagger UI) and their Authorize button, in dev and CI only (#656, A-15).

A deployed environment publishes no docs, no OpenAPI document and no OAuth2 redirect page. Locally,
the OpenAPI document declares Keycloak's authorization-code flow against the browser-facing issuer,
and Swagger UI signs in with PKCE as the public client `sbam-swagger`. Only the dev seed
(compose/keycloak/seed.sh) creates that client, with the `sbam-api` audience and the `organization`
scope, so the token is the same kind the web app sends and RBAC applies unchanged.
"""

from collections.abc import Callable
from typing import Any, Final

from fastapi import FastAPI
from sbam_contracts.api import API_BASE

from sbam_api.core.settings import Settings, deployed_env

SWAGGER_CLIENT_ID: Final = "sbam-swagger"
SCHEME: Final = "keycloak"
DOCS_URL: Final = f"{API_BASE}/docs"
OPENAPI_URL: Final = f"{API_BASE}/openapi.json"
OAUTH2_REDIRECT_URL: Final = f"{DOCS_URL}/oauth2-redirect"


def docs_options(settings: Settings) -> dict[str, Any]:
    """FastAPI's docs arguments: everything off when deployed, PKCE sign-in otherwise."""
    if deployed_env(settings.sbam_env):
        return {"docs_url": None, "openapi_url": None, "swagger_ui_oauth2_redirect_url": None}
    return {
        "docs_url": DOCS_URL,
        "openapi_url": OPENAPI_URL,
        "swagger_ui_oauth2_redirect_url": OAUTH2_REDIRECT_URL,
        "swagger_ui_init_oauth": {
            "clientId": SWAGGER_CLIENT_ID,
            "usePkceWithAuthorizationCodeGrant": True,
            "scopes": "openid",
        },
    }


def security_scheme(settings: Settings) -> dict[str, Any]:
    realm = str(settings.keycloak_issuer).rstrip("/")
    return {
        "type": "oauth2",
        "description": f"Keycloak sign-in as a seeded user (client `{SWAGGER_CLIENT_ID}`, PKCE).",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": f"{realm}/protocol/openid-connect/auth",
                "tokenUrl": f"{realm}/protocol/openid-connect/token",
                "scopes": {"openid": "OpenID Connect"},
            }
        },
    }


def install_docs_security(app: FastAPI, settings: Settings) -> None:
    """Add the scheme, required by every operation, to the published OpenAPI document."""
    if deployed_env(settings.sbam_env):
        return
    build: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = build()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[SCHEME] = (
            security_scheme(settings)
        )
        schema["security"] = [{SCHEME: ["openid"]}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
