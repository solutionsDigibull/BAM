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
    env.setenv("UPLOAD_TOKEN_KEY", "k" * 40)  # required once deployed (A-14)
    assert load_settings().llm_provider == provider


def test_e1_15_t09_unknown_provider_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(SettingsError, match="LLM_PROVIDER"):
        load_settings()


# A-27: the mock runs only where SBAM_ENV is on the allowlist; every other spelling is deployed.
@pytest.mark.parametrize(
    "sbam_env",
    [" production", "production ", "prod-eu", "prd", "live", "stage", "staging-2", "uat", "local"],
)
def test_a27_t01_mock_provider_refused_for_every_non_dev_spelling(
    env: pytest.MonkeyPatch, sbam_env: str
) -> None:
    env.setenv("SBAM_ENV", sbam_env)
    env.setenv("LLM_PROVIDER", "mock")
    with pytest.raises(SettingsError, match="LLM_PROVIDER=mock is forbidden"):
        load_settings()


@pytest.mark.parametrize("sbam_env", ["development", "test", "ci", "CI", " Development "])
def test_a27_t02_mock_provider_runs_in_dev_spellings(
    env: pytest.MonkeyPatch, sbam_env: str
) -> None:
    env.setenv("SBAM_ENV", sbam_env)
    env.setenv("LLM_PROVIDER", "mock")
    assert load_settings().llm_provider == "mock"


def test_a27_t03_blank_sbam_env_is_not_a_dev_env(env: pytest.MonkeyPatch) -> None:
    env.setenv("SBAM_ENV", "   ")
    env.setenv("LLM_PROVIDER", "mock")
    with pytest.raises(SettingsError):
        load_settings()


# A-09: a template secret ("change-me-...") must not run in a deployed environment.
@pytest.mark.parametrize(
    "name", ["STORAGE_SIGNING_KEY", "KEYCLOAK_ADMIN_CLIENT_SECRET", "TUSD_HOOK_SECRET"]
)
@pytest.mark.parametrize("sbam_env", ["staging", "production", "prod-eu", " production"])
def test_a09_t01_placeholder_secret_refused_when_deployed(
    env: pytest.MonkeyPatch, name: str, sbam_env: str
) -> None:
    env.setenv("SBAM_ENV", sbam_env)
    env.setenv("LLM_PROVIDER", "bifrost")
    env.setenv(name, "Change-Me-secret-0000")  # any case
    with pytest.raises(SettingsError) as info:
        load_settings()
    assert name in str(info.value) and "Change-Me" not in str(info.value)


def test_a09_t02_placeholder_secret_still_works_in_dev(env: pytest.MonkeyPatch) -> None:
    env.setenv("SBAM_ENV", "development")
    env.setenv("TUSD_HOOK_SECRET", "change-me-tusd")
    assert load_settings().tusd_hook_secret is not None


def test_a09_t03_real_secret_starts_when_deployed(env: pytest.MonkeyPatch) -> None:
    env.setenv("SBAM_ENV", "production")
    env.setenv("LLM_PROVIDER", "bifrost")
    env.setenv("TUSD_HOOK_SECRET", "9f2c" * 16)
    env.setenv("UPLOAD_TOKEN_KEY", "7b1e" * 16)
    assert load_settings().tusd_hook_secret is not None


# A-14: upload tokens are signed with their own key, not the tusd hook secret.
def _deployed(env: pytest.MonkeyPatch, **values: str) -> None:
    env.setenv("SBAM_ENV", "production")
    env.setenv("LLM_PROVIDER", "bifrost")
    env.setenv("TUSD_HOOK_SECRET", "hook-" + "a" * 40)
    for name, value in values.items():
        env.setenv(name.upper(), value)


def test_a14_t01_deployed_requires_its_own_upload_token_key(env: pytest.MonkeyPatch) -> None:
    _deployed(env)
    with pytest.raises(SettingsError, match="UPLOAD_TOKEN_KEY is required"):
        load_settings()


def test_a14_t02_deployed_refuses_the_hook_secret_reused_as_the_token_key(
    env: pytest.MonkeyPatch,
) -> None:
    _deployed(env, upload_token_key="hook-" + "a" * 40)
    with pytest.raises(SettingsError, match="must differ"):
        load_settings()


def test_a14_t03_the_two_secrets_are_independent_when_deployed(env: pytest.MonkeyPatch) -> None:
    _deployed(env, upload_token_key="token-" + "b" * 40)
    settings = load_settings()
    assert settings.upload_token_secret() == "token-" + "b" * 40
    assert settings.upload_token_secret() != settings.tusd_hook_secret.get_secret_value()  # type: ignore[union-attr]


def test_a14_t04_dev_falls_back_to_the_hook_secret_and_prefers_its_own_key(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("SBAM_ENV", "development")
    env.setenv("TUSD_HOOK_SECRET", "hook-dev")
    assert load_settings().upload_token_secret() == "hook-dev"
    env.setenv("UPLOAD_TOKEN_KEY", "token-dev")
    assert load_settings().upload_token_secret() == "token-dev"
    env.delenv("TUSD_HOOK_SECRET")
    assert load_settings().upload_token_secret() == "token-dev"


def test_a14_t05_no_secret_at_all_means_uploads_off(env: pytest.MonkeyPatch) -> None:
    env.setenv("SBAM_ENV", "development")
    assert load_settings().upload_token_secret() is None


def test_a15_one_replica_may_use_a_per_process_cursor_key(env: pytest.MonkeyPatch) -> None:
    first, second = load_settings(), load_settings()
    assert first.sbam_api_replicas == 1
    assert first.cursor_secret.get_secret_value() != second.cursor_secret.get_secret_value()


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_a15_several_replicas_need_a_shared_cursor_secret(
    env: pytest.MonkeyPatch, blank: str | None
) -> None:
    env.setenv("SBAM_API_REPLICAS", "2")
    if blank is not None:
        env.setenv("CURSOR_SECRET", blank)  # Compose passes an unset variable as ""
    with pytest.raises(SettingsError, match="CURSOR_SECRET"):
        load_settings()


def test_a15_several_replicas_with_a_secret_start(env: pytest.MonkeyPatch) -> None:
    env.setenv("SBAM_API_REPLICAS", "3")
    env.setenv("CURSOR_SECRET", "k" * 32)
    assert load_settings().cursor_secret.get_secret_value() == "k" * 32


def test_a15_a_blank_cursor_secret_never_becomes_the_key(env: pytest.MonkeyPatch) -> None:
    env.setenv("CURSOR_SECRET", "")
    assert len(load_settings().cursor_secret.get_secret_value()) == 64  # the random per-process key
