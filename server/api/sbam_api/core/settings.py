"""Environment-driven settings. Secrets come from env / Docker secrets only, never from code."""

import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only environments where the deterministic LLM mock (and the dev PostHog shortcut) may run,
# the same list as sbam-llm-mock and `STUB_ENVS`. An allowlist, so a spelling nobody listed
# ("prod-eu", "live", " production") counts as deployed and fails closed.
DEV_ENVS = frozenset({"development", "test", "ci"})


PLACEHOLDER = "change-me"  # what compose/.env.example ships for every secret


def deployed_env(env: str) -> bool:
    """True unless `env` (trimmed, case-insensitive) is one of `DEV_ENVS`."""
    return env.strip().lower() not in DEV_ENVS


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
    # The key that signs upload tokens, separate from the hook secret (A-14) so rotating or leaking
    # one never touches the other. Unset in a dev environment it falls back to the hook secret; a
    # deployed environment must set its own.
    upload_token_key: SecretStr | None = None
    tusd_public_url: str = "http://localhost:1080/files/"
    # List cursors are HMAC-signed (core.conventions.encode_cursor).
    # ponytail: a random key per process when unset, so cursors die on restart (the client starts
    # again at page 1). Replicas must share one key or a cursor signed by one fails on another, so
    # SBAM_API_REPLICAS above 1 refuses to start without CURSOR_SECRET (A-15); blank = unset.
    cursor_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_hex(32)))
    sbam_api_replicas: int = Field(default=1, ge=1)
    max_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    # Per-user request budgets (A-08, core/ratelimit.py), per minute, per API process; 0 = off.
    rate_limit_writes_per_minute: int = Field(default=120, ge=0)
    rate_limit_llm_per_minute: int = Field(default=10, ge=0)
    # access_denied audit rows written per actor per minute; further denials are only logged.
    audit_denied_per_minute: int = Field(default=30, ge=0)
    # The masters workbook import (E3.3) scans the upload with clamd before reading it. Unreachable
    # clamd = the import answers 503 SCAN_UNAVAILABLE; nothing is read unscanned.
    clamav_host: str = Field(default="clamav", min_length=1)
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout: float = Field(default=60.0, gt=0)
    log_level: str = "INFO"
    # OpenTelemetry (#35): spans are exported only when the endpoint is set (Compose `obs` profile).
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_service_name: str = Field(default="sbam-api", min_length=1)
    # LLM gateway guard (#46, E1.15-T09): the mock provider is for dev and CI only. Fail closed: an
    # unset LLM_PROVIDER counts as `mock`, so staging/production must set LLM_PROVIDER=bifrost.
    sbam_env: str = Field(default="development", min_length=1)
    llm_provider: Literal["mock", "bifrost"] = "mock"
    # Product analytics (E16.2): server-side PostHog events from the outbox. Unset host or key = no
    # events (a laptop without the `analytics` profile). The salt hashes user ids into distinct_ids.
    posthog_host: str | None = None  # Compose passes "" when unset: a str, not a URL
    posthog_api_key: SecretStr | None = None
    posthog_distinct_id_salt: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_cursor_secret_is_unset(cls, data: Any) -> Any:
        if isinstance(data, dict) and not _set(data.get("cursor_secret")):
            data = {k: v for k, v in data.items() if k != "cursor_secret"}
        return data

    @model_validator(mode="after")
    def _replicas_share_one_cursor_secret(self) -> "Settings":
        if self.sbam_api_replicas > 1 and "cursor_secret" not in self.model_fields_set:
            raise SettingsError(
                f"CURSOR_SECRET is required when SBAM_API_REPLICAS={self.sbam_api_replicas}: "
                "every replica must sign and verify list cursors with the same key."
            )
        return self

    @model_validator(mode="after")
    def _refuse_placeholder_secrets_outside_dev(self) -> "Settings":
        if deployed_env(self.sbam_env):
            secrets_by_name = {
                "STORAGE_SIGNING_KEY": self.storage_signing_key,
                "KEYCLOAK_ADMIN_CLIENT_SECRET": self.keycloak_admin_client_secret,
                "TUSD_HOOK_SECRET": self.tusd_hook_secret,
                "UPLOAD_TOKEN_KEY": self.upload_token_key,
            }
            bad = [n for n, v in secrets_by_name.items() if _is_placeholder(v)]
            if bad:  # names only, never values
                raise SettingsError(
                    f"{', '.join(bad)} still hold the `{PLACEHOLDER}` template value "
                    f"while SBAM_ENV={self.sbam_env}. Provision real secrets."
                )
        return self

    def upload_token_secret(self) -> str | None:
        """The key that signs and verifies upload tokens; None = uploads are off (503)."""
        key = self.upload_token_key or self.tusd_hook_secret  # the fallback is dev-only (validator)
        return key.get_secret_value() if key else None

    @model_validator(mode="after")
    def _refuse_mock_provider_outside_dev(self) -> "Settings":
        if self.llm_provider == "mock" and deployed_env(self.sbam_env):
            raise SettingsError(
                f"LLM_PROVIDER=mock is forbidden when SBAM_ENV={self.sbam_env}. "
                "Set LLM_PROVIDER=bifrost."
            )
        posthog_problem(
            self.sbam_env, self.posthog_host, self.posthog_api_key, self.posthog_distinct_id_salt
        )
        return self

    @model_validator(mode="after")
    def _require_separate_upload_token_key_when_deployed(self) -> "Settings":
        if not deployed_env(self.sbam_env):
            return self
        if not _set(self.upload_token_key):
            raise SettingsError(
                f"UPLOAD_TOKEN_KEY is required when SBAM_ENV={self.sbam_env} "
                "(it must differ from TUSD_HOOK_SECRET)."
            )
        if (
            self.upload_token_key
            and self.tusd_hook_secret
            and (
                self.upload_token_key.get_secret_value() == self.tusd_hook_secret.get_secret_value()
            )
        ):
            raise SettingsError("UPLOAD_TOKEN_KEY must differ from TUSD_HOOK_SECRET.")
        return self


def _set(value: object) -> bool:
    """A setting holds something: an empty string or an empty secret counts as unset."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return bool(str(value).strip()) if value is not None else False


def _is_placeholder(value: object) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return isinstance(value, str) and PLACEHOLDER in value.lower()


def posthog_problem(env: str, host: object, key: object, salt: SecretStr | None) -> None:
    """Fail fast when PostHog would send outside development without a salt (raw ids at risk)."""
    if _set(host) and _set(key) and deployed_env(env) and not _set(salt):
        raise SettingsError(
            f"POSTHOG_DISTINCT_ID_SALT is required when PostHog is on and SBAM_ENV={env}."
        )


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
