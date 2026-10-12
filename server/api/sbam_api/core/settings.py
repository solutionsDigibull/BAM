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
    keycloak_issuer: AnyHttpUrl
    keycloak_audience: str = Field(min_length=1)
    # The issuer above must equal the tokens' `iss` (the browser-facing URL). The API fetches keys
    # from inside the network, so the JWKS URL can differ; default: <issuer>/protocol/.../certs.
    keycloak_jwks_url: AnyHttpUrl | None = None
    max_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_level: str = "INFO"
    # OpenTelemetry (#35): spans are exported only when the endpoint is set (Compose `obs` profile).
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_service_name: str = Field(default="sbam-api", min_length=1)


def jwks_url(settings: Settings) -> str:
    if settings.keycloak_jwks_url:
        return str(settings.keycloak_jwks_url)
    return f"{str(settings.keycloak_issuer).rstrip('/')}/protocol/openid-connect/certs"


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
