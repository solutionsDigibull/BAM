"""E3.8 master documents against the Compose stack: the real sbam-api, Postgres, storage, Keycloak
and ClamAV (TD §19.3).

The master-document attach is synchronous (unlike the Temporal ingestion in test_scan_stack.py): the
API scans inline, retrying `SCAN_ATTEMPTS` times. T07 stops ClamAV so the scan gets no verdict in
those tries; the document is then stored `SCAN_FAILED` with `parse_status = PENDING`, the reroute
PATCH answers 409 (not scanned clean), and nothing is parsed. ClamAV is started again at the end and
waited for, like test_scan_stack.py, so the stack is whole for whatever runs next.
"""

import hashlib
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fixtures import fixture_path

from .conftest import Compose
from .test_identity import API, admin_secret, sql
from .test_keycloak_realm import login

pytestmark = pytest.mark.integration


@pytest.fixture
def admin() -> str:
    admin_secret()  # the stack must have sbam-api-admin, or the fixture skips
    token: str = login("sbam-admin", "admin-a")["access_token"]
    return token


def call(method: str, path: str, token: str, **kwargs: object) -> httpx.Response:
    headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}  # type: ignore[dict-item]
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)  # type: ignore[arg-type]


def clamav_health(compose: Compose) -> str:
    return compose("ps", "clamav", "--format", "{{.Health}}").stdout.strip()


@pytest.fixture
def cleanup() -> Iterator[list[str]]:
    """Master-document ids to remove from the seed tenant afterwards, pass or fail."""
    ids: list[str] = []
    try:
        yield ids
    finally:
        for doc_id in ids:
            sql("DELETE FROM sbam.master_documents WHERE id = :id", id=doc_id)


def test_e3_8_t07_no_verdict_is_scan_failed_and_never_routable(
    admin: str, compose: Compose, cleanup: list[str]
) -> None:
    # Unique bytes per run so this is always a fresh attach, never a re-scan of an earlier copy.
    data = fixture_path("documents", "sample.txt").read_bytes() + f"\n{uuid.uuid4()}\n".encode()
    sha256 = hashlib.sha256(data).hexdigest()

    assert compose("stop", "clamav").returncode == 0
    try:
        name = f"t07-{uuid.uuid4().hex}.txt"
        res = call(
            "POST",
            "/admin/master-documents",
            admin,
            files={"file": (name, data, "text/plain")},
        )
        assert res.status_code == 201, res.text  # stored, not an error: the FDE can retry the scan
        body = res.json()
        cleanup.append(body["id"])
        assert body["scan_status"] == "SCAN_FAILED"
        assert body["parse_status"] == "PENDING"  # never parsed without a clean verdict
        assert body["sha256"] == sha256

        # The route cannot be confirmed or overridden while the file is not scanned clean: 409.
        patch = call(
            "PATCH",
            f"/admin/master-documents/{body['id']}",
            admin,
            headers={"if-match": res.headers["etag"]},
            json={"master_type": "RULES"},
        )
        assert patch.status_code == 409, patch.text
        assert patch.json()["code"] == "CONFLICT"

        # It is still SCAN_FAILED / PENDING when read back: the failed attempt changed nothing else.
        got = call("GET", f"/admin/master-documents/{body['id']}", admin)
        assert got.status_code == 200, got.text
        assert (got.json()["scan_status"], got.json()["parse_status"]) == ("SCAN_FAILED", "PENDING")
    finally:
        compose("start", "clamav")
        deadline = time.monotonic() + 420  # freshclam may need a while after a restart
        while time.monotonic() < deadline and clamav_health(compose) != "healthy":
            time.sleep(5)
    assert clamav_health(compose) == "healthy"  # the stack is whole again for whatever runs next
