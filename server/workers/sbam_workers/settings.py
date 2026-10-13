"""Worker settings from env (Compose passes them one by one, never an env_file; ADR 0001)."""

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
