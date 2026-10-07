"""Spike API: `get_principal()` plus two demo routes. Run: `uv run uvicorn app:app --port 8100`."""

import logging
import os
from typing import Annotated, Any

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from auth import InvalidTokenError, Principal, TokenVerifier

ISSUER = os.environ.get("SPIKE_ISSUER", "http://localhost:8180/realms/bullm")
AUDIENCE = os.environ.get("SPIKE_AUDIENCE", "sbam-api")
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"

log = logging.getLogger("spike.auth")


class ProblemError(Exception):
    def __init__(self, status: int, title: str, headers: dict[str, str] | None = None) -> None:
        self.status, self.title, self.headers = status, title, headers


def create_app(verifier: TokenVerifier) -> FastAPI:
    app = FastAPI(title="SBAM identity spike")

    @app.exception_handler(ProblemError)
    async def problem(_: Request, exc: ProblemError) -> JSONResponse:
        body = {"type": "about:blank", "title": exc.title, "status": exc.status}
        return JSONResponse(
            body, exc.status, headers=exc.headers, media_type="application/problem+json"
        )

    def get_principal(request: Request) -> Principal:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ProblemError(401, "Unauthorized", {"WWW-Authenticate": "Bearer"})
        try:
            return verifier.verify(token)
        except InvalidTokenError as exc:
            log.info("token rejected: %s", exc)
            raise ProblemError(401, "Unauthorized", {"WWW-Authenticate": "Bearer"}) from None

    CurrentPrincipal = Annotated[Principal, Depends(get_principal)]  # noqa: N806

    @app.get("/me")
    def me(principal: CurrentPrincipal) -> dict[str, Any]:
        return {
            "sub": principal.subject,
            "username": principal.username,
            "client_id": principal.client_id,
            "tenant_id": principal.tenant_id,
            "roles": sorted(principal.roles),
        }

    @app.get("/tenants-demo/{tenant_id}")
    def tenant_resource(tenant_id: str, principal: CurrentPrincipal) -> dict[str, str]:
        # Cross-tenant and tenant-less tokens get 404, never 403: don't confirm it exists.
        if principal.tenant_id is None or principal.tenant_id != tenant_id:
            raise ProblemError(404, "Not Found")
        return {"tenant_id": tenant_id, "data": f"demo resource of {tenant_id}"}

    return app


app = create_app(TokenVerifier(ISSUER, AUDIENCE, jwt.PyJWKClient(JWKS_URL, lifespan=300)))
