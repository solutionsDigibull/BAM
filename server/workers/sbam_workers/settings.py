"""Worker settings from env (Compose passes them one by one, never an env_file; ADR 0001)."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sbam_api.core.settings import posthog_problem


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    temporal_host: str = Field(pattern=r"^[^:\s]+:\d+$", description="host:port")
    temporal_namespace: str = Field(default="sbam", min_length=1)
    database_url: str = Field(min_length=1)  # the run mirror (temporal_runs)
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_service_name: str = Field(default="sbam-worker", min_length=1)
    heartbeat_file: str = "/tmp/sbam/heartbeat"  # noqa: S108 - read by the image healthcheck
    heartbeat_seconds: float = Field(default=10.0, gt=0)
    # Storage (#62): only the ingest worker mounts it. Same switch as the API.
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: Path | None = None
    s3_bucket: str | None = None
    s3_endpoint_url: AnyHttpUrl | None = None
    # Keycloak events into the audit log (A-18b): only the ingest worker is given the secret.
    # Without it the import does nothing. The URL is the realm inside the network, like the API's.
    keycloak_internal_url: AnyHttpUrl | None = None
    keycloak_admin_client_id: str = Field(default="sbam-api-admin", min_length=1)
    keycloak_admin_client_secret: SecretStr | None = None
    # ClamAV (#62): clamd on the internal network; scans are refused when its signatures are older.
    clamav_host: str = Field(default="clamav", min_length=1)
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=60.0, gt=0)
    # ClamAV publishes one database a day (about 06:25 UTC) and freshclam checks every 2 h, so a
    # 24 h limit would refuse scans for a few hours on most days (#62).
    clamav_max_signature_age_hours: float = Field(default=48.0, gt=0)
    # Static gate (E12.4b): a shared directory the `sbam-toolchain` container watches. Empty runs
    # Ruff, MyPy and Bandit as child processes of the worker (laptops, tests).
    toolchain_jobs_dir: str = ""
    toolchain_timeout_seconds: float = Field(default=300.0, gt=0)
    # LLM layer (E6.1): every call goes through Bifrost with an `sbam-*` alias. Defaults match
    # Compose.
    bifrost_url: AnyHttpUrl = AnyHttpUrl("http://bifrost:8080")
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_context_tokens: int = Field(default=200_000, ge=1000)
    # Cost control. Identical input (same prompt, schema, documents, alias) is answered from
    # `llm_derivatives` for the whole tenant instead of being billed again. The budgets are opt-in
    # (C-41: no limit by default): once the recorded spend of a project or tenant reaches one, the
    # next model call is refused (non-retryable) until an admin raises it. Cache hits cost nothing.
    llm_derivative_cache: bool = True
    # `allow_inf_nan=False`: NaN would make every comparison false (a budget that never trips) and
    # Infinity is a limit that never trips; both are refused at start. Compose passes "" when unset.
    llm_project_budget_usd: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    llm_tenant_budget_usd: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    # What a call costs when Bifrost reports none (a model its catalogue has no price for): USD per
    # million tokens, per alias, as JSON: {"sbam-sow": {"in": 2.5, "out": 10}}. Only read while a
    # budget is set. Without an entry such a call is refused from then on (`BudgetCostUnknown`).
    # NoDecode: the env value is parsed below, so Compose's empty "" means unset, not a JSON error.
    llm_price_usd_per_mtok: Annotated[dict[str, dict[str, Decimal]], NoDecode] = Field(
        default_factory=dict
    )
    # Transport errors, 5xx and 429s are tried this many times inside the wrapper, then surface as a
    # retryable `LlmTransportError` (the Temporal retry policy takes over).
    llm_transport_attempts: int = Field(default=3, ge=1, le=10)
    llm_backoff_seconds: float = Field(default=1.0, ge=0)
    # Langfuse (E6.1): traces over its REST API. Unset keys = no export; the trace id is still made.
    # One project per environment (E16.2), checked before polling by `llm.preflight`.
    langfuse_host: str | None = None  # Compose passes "" when unset: a str, not a URL
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    # Redaction policy: prompts and answers hold client documents, so they are exported only as
    # sizes and hashes unless this is switched on (development).
    langfuse_log_content: bool = False
    # SCHEMA_SQL gate (E9.4): the isolated `schema-check` PostgreSQL the build worker applies each
    # Workflow's schema.sql to, in a throwaway database. Never SBAM's own database (refused).
    schema_check_dsn: SecretStr | None = None
    # Environment and product analytics (E16.2): every worker dispatches the outbox, so each one
    # sends the PostHog events of the rows it dispatches. Same checks as sbam-api.
    sbam_env: str = Field(default="development", min_length=1)
    posthog_host: str | None = None  # Compose passes "" when unset: a str, not a URL
    posthog_api_key: SecretStr | None = None
    posthog_distinct_id_salt: SecretStr | None = None

    @field_validator("llm_project_budget_usd", "llm_tenant_budget_usd", mode="before")
    @classmethod
    def _empty_budget_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_price_usd_per_mtok", mode="before")
    @classmethod
    def _prices_from_json(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value) if value.strip() else {}
            except ValueError:
                raise ValueError('must be a JSON object: {"alias": {"in": 1, "out": 2}}') from None
        return value

    @field_validator("llm_price_usd_per_mtok")
    @classmethod
    def _prices_are_plain_numbers(
        cls, value: dict[str, dict[str, Decimal]]
    ) -> dict[str, dict[str, Decimal]]:
        for alias, price in value.items():
            if set(price) != {"in", "out"}:
                raise ValueError(f"{alias}: give exactly 'in' and 'out' (USD per million tokens)")
            for side, usd in price.items():
                if not usd.is_finite() or usd < 0:
                    raise ValueError(f"{alias}.{side}: must be a finite number of USD, 0 or more")
        return value

    @model_validator(mode="after")
    def _posthog_needs_a_salt(self) -> "WorkerSettings":
        posthog_problem(
            self.sbam_env, self.posthog_host, self.posthog_api_key, self.posthog_distinct_id_salt
        )
        return self
