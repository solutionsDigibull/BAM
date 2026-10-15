"""E4.3 against the Compose stack: the real ClamAV, tusd, the API, the ingest worker and Temporal.

An upload goes through tusd as in test_uploads_stack.py; its post-finish hook starts
DocumentIngestion, whose first step is the scan. The tests then watch the version row.
The test that stops ClamAV is last in the file: it starts ClamAV again and waits for it.
"""

import os
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fixtures import eicar_bytes
from sbam_api.ingest.models import DocumentVersion

from .conftest import Compose
from .test_uploads_stack import Scope, create, patch

pytestmark = pytest.mark.integration

SCAN_WAIT = 120  # seconds: workflow start, scan and state change


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def upload(scope: Scope, name: str, data: bytes) -> uuid.UUID:
    token = scope.slot(name, len(data))
    status, headers, _ = create(scope, token, name, len(data))
    assert status == 201, headers
    assert patch(headers["Location"], 0, data) == 204
    return uuid.UUID(headers["Location"].rsplit("/", 1)[1])


def settled(scope: Scope, version_id: uuid.UUID, seconds: int = SCAN_WAIT) -> DocumentVersion:
    """The version once it has a scan verdict (or when the wait is over)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for version in scope.versions():
            if version.id == version_id and version.scan_status is not None:
                return version
        time.sleep(1)
    return next(v for v in scope.versions() if v.id == version_id)


def listing(compose: Compose, scope: Scope, kind: str) -> str:
    path = f"/data/storage/{scope.tenant}/{scope.project}/{kind}"
    result = compose("exec", "-T", "sbam-api", "sh", "-c", f"ls {path} 2>/dev/null || true")
    return result.stdout


def test_e4_3_t01_clean_file_is_scanned_and_moves_on_to_parsing(scope: Scope) -> None:
    version_id = upload(scope, "clean.pdf", b"%PDF-1.7\n" + os.urandom(4096))
    version = settled(scope, version_id)
    assert version.scan_status == "CLEAN" and version.state == "PARSING"
    assert version.scan_signature_version and version.scan_signature_version.startswith("ClamAV")
    assert version.scanned_at is not None and version.reject_code is None


def test_e4_3_t02_eicar_is_rejected_quarantined_and_never_parsed(
    scope: Scope, compose: Compose
) -> None:
    version_id = upload(scope, "eicar.com.txt", eicar_bytes())  # the 68-byte test file
    version = settled(scope, version_id)
    assert version.scan_status == "INFECTED" and version.state == "REJECTED"
    assert version.reject_code == "MALWARE"
    assert "/quarantine/" in version.storage_key
    assert version.sha256 in listing(compose, scope, "quarantine")
    assert version.sha256 not in listing(compose, scope, "uploads")  # no longer in uploads/


def test_e4_3_t08_a_15_mb_file_is_scanned_in_under_30_seconds(scope: Scope) -> None:
    version_id = upload(scope, "max.pdf", os.urandom(15_728_640))
    version = settled(scope, version_id)
    assert version.scan_status == "CLEAN"  # clamd's stream limit is above the 15 MB cap
    assert version.scanned_at is not None
    assert utc(version.scanned_at) - utc(version.created_at) < timedelta(seconds=30)


def clamav_health(compose: Compose) -> str:
    return compose("ps", "clamav", "--format", "{{.Health}}").stdout.strip()


def test_e4_3_t03_clamd_stopped_three_attempts_then_scan_failed(
    scope: Scope, compose: Compose
) -> None:
    assert compose("stop", "clamav").returncode == 0
    try:
        version_id = upload(scope, "unscanned.pdf", os.urandom(2048))
        version = settled(scope, version_id, seconds=180)
        assert version.scan_status == "SCAN_FAILED" and version.state == "SCAN_FAILED"
        assert version.reject_code is None  # not rejected: the FDE can retry
    finally:
        compose("start", "clamav")
        deadline = time.monotonic() + 420  # freshclam may need a while after a restart
        while time.monotonic() < deadline and clamav_health(compose) != "healthy":
            time.sleep(5)
    assert clamav_health(compose) == "healthy"  # the stack is whole again for whatever runs next
