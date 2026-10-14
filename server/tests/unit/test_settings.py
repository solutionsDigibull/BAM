"""E1.3-T07: the app refuses to start with missing or invalid settings."""

from pathlib import Path

import pytest
from sbam_api.core.settings import SettingsError, load_settings

VALID = {
    "DATABASE_URL": "postgresql://sbam:s3cret@db:5432/sbam",
    "STORAGE_ROOT": "/data/storage",
    "TEMPORAL_HOST": "temporal:7233",
    "KEYCLOAK_ISSUER": "http://keycloak:8080/realms/sbam",
    "KEYCLOAK_AUDIENCE": "sbam-api",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)  # no stray .env
    for key, value in VALID.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_e1_3_t07_valid_settings_load(env: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.temporal_host == "temporal:7233"
    assert settings.max_body_bytes == 10 * 1024 * 1024


def test_e1_3_t07_missing_database_url_fails_clearly(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()


def test_e1_3_t07_invalid_values_named_but_not_echoed(env: pytest.MonkeyPatch) -> None:
    env.setenv("TEMPORAL_HOST", "no-port-here")
    env.setenv("KEYCLOAK_ISSUER", "not a url")
    with pytest.raises(SettingsError) as info:
        load_settings()
    message = str(info.value)
    assert "TEMPORAL_HOST" in message and "KEYCLOAK_ISSUER" in message
    assert "s3cret" not in message and "no-port-here" not in message


@pytest.mark.parametrize("sbam_env", ["production", "staging", "PRODUCTION", "prod"])
def test_e1_15_t09_mock_provider_refused_in_staging_and_production(
    env: pytest.MonkeyPatch, sbam_env: str
) -> None:
    env.setenv("SBAM_ENV", sbam_env)
    env.setenv("LLM_PROVIDER", "mock")
    with pytest.raises(SettingsError, match="LLM_PROVIDER=mock is forbidden"):
        load_settings()


def test_e1_15_t09_unset_provider_counts_as_mock_so_production_fails_closed(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("SBAM_ENV", "production")
    env.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(SettingsError, match="forbidden"):
        load_settings()


@pytest.mark.parametrize(
    ("sbam_env", "provider"),
    [("development", "mock"), ("ci", "mock"), ("production", "bifrost"), ("staging", "bifrost")],
)
def test_e1_15_t09_other_combinations_start(
    env: pytest.MonkeyPatch, sbam_env: str, provider: str
) -> None:
    env.setenv("SBAM_ENV", sbam_env)
    env.setenv("LLM_PROVIDER", provider)
    assert load_settings().llm_provider == provider


def test_e1_15_t09_unknown_provider_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(SettingsError, match="LLM_PROVIDER"):
        load_settings()
