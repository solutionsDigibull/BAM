"""N-02b (#609): bulkhead limits and wait are settings; the defaults are the old constants."""

import base64
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from sbam_api.core import bulkhead
from sbam_api.core.bulkhead import CLAMD, KEYCLOAK_ADMIN, KEYCLOAK_JWKS, BulkheadFullError
from sbam_api.core.settings import Settings, SettingsError, load_settings

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
    monkeypatch.setenv("SECRETS_KEYS", "*k1:" + base64.b64encode(bytes(range(32))).decode())
    return monkeypatch


@pytest.fixture(autouse=True)
def restore() -> Iterator[None]:
    """The bulkheads are process-wide: put the defaults back whatever a test configured."""
    yield
    for dep, limit in ((CLAMD, 8), (KEYCLOAK_ADMIN, 8), (KEYCLOAK_JWKS, 8)):
        dep.resize(limit, 0.5)


def test_n02b_sizing_defaults_are_the_old_constants(env: pytest.MonkeyPatch) -> None:
    bulkhead.configure(load_settings())
    assert (CLAMD.limit, KEYCLOAK_ADMIN.limit, KEYCLOAK_JWKS.limit) == (8, 8, 8)
    assert CLAMD.wait == 0.5
    CLAMD.wait = 0.05  # keep the 9th attempt quick
    with ExitStack() as stack:
        for _ in range(8):
            stack.enter_context(CLAMD.slot())
        with pytest.raises(BulkheadFullError), CLAMD.slot():
            pass


def test_n02b_sizing_a_settings_override_changes_the_limit(env: pytest.MonkeyPatch) -> None:
    env.setenv("BULKHEAD_CLAMD_LIMIT", "2")
    env.setenv("BULKHEAD_WAIT_SECONDS", "0.05")
    settings: Settings = load_settings()
    bulkhead.configure(settings)
    assert (CLAMD.limit, CLAMD.wait) == (2, 0.05)
    assert KEYCLOAK_ADMIN.limit == 8 and KEYCLOAK_JWKS.limit == 8
    with CLAMD.slot(), CLAMD.slot():
        assert CLAMD.saturated
        with pytest.raises(BulkheadFullError), CLAMD.slot():
            pass


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BULKHEAD_CLAMD_LIMIT", "0"),
        ("BULKHEAD_CLAMD_LIMIT", "65"),
        ("BULKHEAD_KEYCLOAK_ADMIN_LIMIT", "0"),
        ("BULKHEAD_KEYCLOAK_JWKS_LIMIT", "65"),
        ("BULKHEAD_WAIT_SECONDS", "-1"),
        ("BULKHEAD_WAIT_SECONDS", "31"),
    ],
)
def test_n02b_sizing_out_of_range_is_refused_at_startup(
    env: pytest.MonkeyPatch, name: str, value: str
) -> None:
    env.setenv(name, value)
    with pytest.raises(SettingsError, match=name):  # load_settings wraps pydantic's error
        load_settings()
