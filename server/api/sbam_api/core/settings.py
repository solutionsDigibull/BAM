"""Environment-driven settings. Secrets come from env / Docker secrets only, never from code."""

from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Startup configuration is missing or invalid. Names the variables, never their values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)
    storage_root: Path
    # Storage (#37): switching to S3 is config only. No signing key = signed downloads off (503).
    storage_backend: Literal["local", "s3"] = "local"
    storage_signing_key: SecretStr | None = None
    s3_bucket: str | None = None
    s3_endpoint_url: AnyHttpUrl | None = None
    temporal_host: str = Field(pattern=r"^[^:\s]+:\d+$", description="host:port")
    # Temporal (#40): Compose creates namespace `sbam` and registers the search attributes.
    temporal_namespace: str = Field(default="sbam", min_length=1)
    temporal_search_attributes: bool = True
    enable_ping: bool = False  # sample command POST …/runs/ping; local/dev only
    keycloak_issuer: AnyHttpUrl
    keycloak_audience: str = Field(min_length=1)
    # The issuer above must equal the tokens' `iss` (the browser-facing URL). The API fetches keys
    # from inside the network, so the JWKS URL can differ; default: <issuer>/protocol/.../certs.
    keycloak_jwks_url: AnyHttpUrl | None = None
    # Tenant creation (#49) manages Keycloak organizations as a service account. The realm URL as
    # seen from inside the network (default: the issuer). No secret = tenant creation answers 503.
    keycloak_internal_url: AnyHttpUrl | None = None
    keycloak_admin_client_id: str = Field(default="sbam-api-admin", min_length=1)
    keycloak_admin_client_secret: SecretStr | None = None
    # Uploads (#61): tusd calls the hook endpoint with this secret; no secret = hooks and upload
    # tokens answer 503. The browser reaches tusd at `tusd_public_url`.
    tusd_hook_secret: SecretStr | None = None
    tusd_public_url: str = "http://localhost:1080/files/"
    max_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_level: str = "INFO"
    # OpenTelemetry (#35): spans are exported only when the endpoint is set (Compose `obs` profile).
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_service_name: str = Field(default="sbam-api", min_length=1)


def jwks_url(settings: Settings) -> str:
    if settings.keycloak_jwks_url:
        return str(settings.keycloak_jwks_url)
    return f"{str(settings.keycloak_issuer).rstrip('/')}/protocol/openid-connect/certs"


def keycloak_realm_url(settings: Settings) -> str:
    return str(settings.keycloak_internal_url or settings.keycloak_issuer).rstrip("/")


def load_settings(**overrides: Any) -> Settings:
    """Build settings from env (+ `.env`); overrides win. Raises `SettingsError` on any problem."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        raise SettingsError(
            f"Missing or invalid settings: {', '.join(names)}. "
            "Set them in the environment or server/.env."
        ) from None
