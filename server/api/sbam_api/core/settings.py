"""Environment-driven settings. Secrets come from env / Docker secrets only, never from code."""

from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Startup configuration is missing or invalid. Names the variables, never their values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)
    storage_root: Path
    temporal_host: str = Field(pattern=r"^[^:\s]+:\d+$", description="host:port")
    keycloak_issuer: AnyHttpUrl
    keycloak_audience: str = Field(min_length=1)
    max_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_level: str = "INFO"


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
