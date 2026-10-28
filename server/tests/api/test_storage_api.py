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


# --- #402: a download carries the name and type the browser should use --------------------------

OKF_TYPE = "application/zip"
PDF_TYPE = "application/pdf"


def named(key: str, name: str = "okf-bundle-v1-01a18113.zip", kind: str = OKF_TYPE) -> str:
    return signed_url(SECRET, key, TenantContext(TENANT_A), 60, filename=name, content_type=kind)


def test_e1_6_402_a_named_link_is_saved_and_served_as_that_name_and_type(
    client: TestClient, tmp_path: Path
) -> None:
    key = stored(tmp_path)
    url = named(key)
    query = parse_qs(urlsplit(url).query)
    assert query["name"] == ["okf-bundle-v1-01a18113.zip"] and query["type"] == [OKF_TYPE]
    res = client.get(url)
    assert res.status_code == 200 and res.content == b"report bytes"
    assert res.headers["content-disposition"] == (
        'attachment; filename="okf-bundle-v1-01a18113.zip"; '
        "filename*=UTF-8''okf-bundle-v1-01a18113.zip"
    )  # RFC 6266, from the shared helper
    assert res.headers["content-type"] == OKF_TYPE
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "private, no-store"


def test_e1_6_402_a_link_without_a_name_is_still_a_plain_attachment(
    client: TestClient, tmp_path: Path
) -> None:
    res = client.get(signed_url(SECRET, stored(tmp_path), TenantContext(TENANT_A), 60))
    assert res.headers["content-disposition"] == "attachment"
    assert res.headers["content-type"] == "application/octet-stream"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_e1_6_402_the_name_and_type_are_part_of_the_signature(
    client: TestClient, tmp_path: Path
) -> None:
    key = stored(tmp_path)
    query = parse_qs(urlsplit(named(key, "report.pdf", PDF_TYPE)).query)
    exp, sig = query["exp"][0], query["sig"][0]
    base = f"/api/v1/files/{key}?exp={exp}&sig={sig}"
    other = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    cases = {
        "another name": f"{base}&name=other.pdf&type={PDF_TYPE}",
        "another type": f"{base}&name=report.pdf&type={other}",
        "name removed": f"{base}&type={PDF_TYPE}",
        "type removed": f"{base}&name=report.pdf",
        "both removed": base,
        "an unsafe name": f"{base}&name=..%2Fx.pdf&type={PDF_TYPE}",
        "a type that is not allowed": f"{base}&name=report.pdf&type=text%2Fhtml",
    }
    for case, url in cases.items():
        res = client.get(url)
        assert res.status_code == 403, case
        assert res.json()["code"] == "FORBIDDEN", case
        assert "report bytes" not in res.text, case


def test_e1_6_402_a_name_cannot_be_added_to_a_link_that_was_signed_without_one(
    client: TestClient, tmp_path: Path
) -> None:
    key = stored(tmp_path)
    query = parse_qs(urlsplit(signed_url(SECRET, key, TenantContext(TENANT_A), 60)).query)
    exp, sig = query["exp"][0], query["sig"][0]
    res = client.get(f"/api/v1/files/{key}?exp={exp}&sig={sig}&name=report.pdf&type={PDF_TYPE}")
    assert res.status_code == 403


@pytest.mark.parametrize(
    "name",
    ["", "../x.zip", "a/b.zip", "a b.zip", 'a"b.zip', "a\nb.zip", "a;b.zip", ".hidden", "x" * 101],
)
def test_e1_6_402_an_unsafe_file_name_is_never_signed(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="file name"):
        named(stored(tmp_path), name)


def test_e1_6_402_a_name_needs_a_type_and_the_type_must_be_allowed(tmp_path: Path) -> None:
    key, ctx = stored(tmp_path), TenantContext(TENANT_A)
    with pytest.raises(ValueError, match="both"):
        signed_url(SECRET, key, ctx, 60, filename="a.zip")
    with pytest.raises(ValueError, match="both"):
        signed_url(SECRET, key, ctx, 60, content_type=OKF_TYPE)
    for bad in (
        "text/html",
        "application/octet-stream",
        "image/svg+xml",
        "application/zip\r\nX: y",
    ):
        with pytest.raises(ValueError, match="content type"):
            signed_url(SECRET, key, ctx, 60, filename="a.zip", content_type=bad)


def test_e1_6_402_a_cross_tenant_named_link_is_still_404(tmp_path: Path) -> None:
    key = stored(tmp_path, TENANT_B)
    with pytest.raises(ApiError) as err:
        named(key)
    assert err.value.status == 404
