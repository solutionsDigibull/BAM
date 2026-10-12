"""E1.6 signed download URLs through the real app (`GET /api/v1/files/{key}`)."""

import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sbam_api.core.conventions import ApiError
from sbam_api.db import TenantContext
from sbam_api.main import create_app
from sbam_api.storage import Kind, StorageScope, signed_url
from sbam_api.storage.local import LocalDiskBackend
from test_core_app import ALL_OK, make_settings

SECRET = "test-signing-key-not-a-real-secret"  # noqa: S105
TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, storage_signing_key=SECRET), ALL_OK))


def stored(tmp_path: Path, tenant: uuid.UUID = TENANT_A, data: bytes = b"report bytes") -> str:
    obj = LocalDiskBackend(tmp_path).put(data, StorageScope(tenant, uuid.uuid4()), Kind.EXPORTS, "")
    return obj.storage_key


def test_e1_6_signed_url_downloads_the_object(client: TestClient, tmp_path: Path) -> None:
    key = stored(tmp_path)
    res = client.get(signed_url(SECRET, key, TenantContext(TENANT_A), 60))
    assert res.status_code == 200
    assert res.content == b"report bytes"
    assert res.headers["content-disposition"] == "attachment"


def test_e1_6_t05_tampered_or_expired_url_is_403(client: TestClient, tmp_path: Path) -> None:
    key, other = stored(tmp_path), stored(tmp_path, data=b"someone else's file")
    url = signed_url(SECRET, key, TenantContext(TENANT_A), 900)
    query = parse_qs(urlsplit(url).query)
    exp, sig = query["exp"][0], query["sig"][0]
    tampered_key = client.get(f"/api/v1/files/{other}?exp={exp}&sig={sig}")
    tampered_sig = client.get(f"/api/v1/files/{key}?exp={exp}&sig={'0' * 64}")
    longer = client.get(f"/api/v1/files/{key}?exp={int(exp) + 3600}&sig={sig}")
    expired_url = signed_url(SECRET, key, TenantContext(TENANT_A), 900, now=time.time() - 901)
    expired = client.get(expired_url)
    for res in (tampered_key, tampered_sig, longer, expired):
        assert res.status_code == 403
        assert res.json()["code"] == "FORBIDDEN"
        assert "report bytes" not in res.text


def test_e1_6_t06_cross_tenant_url_is_404_and_never_issued(tmp_path: Path) -> None:
    key = stored(tmp_path, TENANT_B)
    with pytest.raises(ApiError) as err:
        signed_url(SECRET, key, TenantContext(TENANT_A), 60)
    assert err.value.status == 404


def test_e1_6_expiry_must_be_1_to_900_seconds(tmp_path: Path) -> None:
    key = stored(tmp_path)
    for bad in (0, 901):
        with pytest.raises(ValueError):
            signed_url(SECRET, key, TenantContext(TENANT_A), bad)


@pytest.mark.parametrize("configured", [None, "change-me-storage"])
def test_e1_6_missing_or_placeholder_signing_key_turns_downloads_off(
    tmp_path: Path, configured: str | None
) -> None:
    key = stored(tmp_path)
    url = signed_url(SECRET, key, TenantContext(TENANT_A), 60)
    settings = make_settings(tmp_path, storage_signing_key=configured)
    assert TestClient(create_app(settings, ALL_OK)).get(url).status_code == 503
    with pytest.raises(ApiError):
        signed_url(configured, key, TenantContext(TENANT_A), 60)  # and nothing gets signed


def test_e1_6_bad_key_in_url_is_400(client: TestClient) -> None:
    res = client.get("/api/v1/files/..%2F..%2Fetc%2Fpasswd?exp=9999999999&sig=x")
    assert res.status_code in (400, 404)  # rejected by key validation or by routing; never read
