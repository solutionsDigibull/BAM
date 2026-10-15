"""Worker settings from env (Compose passes them one by one, never an env_file; ADR 0001)."""

from pathlib import Path
from typing import Literal

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
    # Storage (#62): only the ingest worker mounts it. Same switch as the API.
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: Path | None = None
    s3_bucket: str | None = None
    s3_endpoint_url: AnyHttpUrl | None = None
    # ClamAV (#62): clamd on the internal network; scans are refused when its signatures are older.
    clamav_host: str = Field(default="clamav", min_length=1)
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=60.0, gt=0)
    # ClamAV publishes one database a day (about 06:25 UTC) and freshclam checks every 2 h, so a
    # 24 h limit would refuse scans for a few hours on most days (#62).
    clamav_max_signature_age_hours: float = Field(default=48.0, gt=0)
