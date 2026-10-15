"""E4.3 unit tests: the clamd client, the signature-age rule, the scan guard and the model."""

import io
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clamd_fake import FakeClamd, version_string
from fixtures import eicar_bytes
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.rules import is_downloadable
from sbam_contracts.documents import ScanOutcome, ScanStatus
from sbam_workers.ingest.clamd import (
    ClamdClient,
    ClamdError,
    SignatureStaleError,
    parse_version,
    require_fresh,
    signature_age,
)
from sbam_workers.ingest.guard import require_clean
from temporalio.exceptions import ApplicationError

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def client(server: FakeClamd) -> ClamdClient:
    return ClamdClient(server.host, server.port, timeout=5)


def test_e4_3_t01_clean_stream_is_ok() -> None:
    with FakeClamd() as server:
        verdict = client(server).scan(io.BytesIO(b"%PDF-1.7 hello"))
    assert not verdict.infected and verdict.signature is None


def test_e4_3_t02_eicar_is_found() -> None:
    with FakeClamd() as server:
        verdict = client(server).scan(io.BytesIO(b"prefix " + eicar_bytes() + b" suffix"))
    assert verdict.infected and verdict.signature == "Win.Test.EICAR_HDB-1"


def test_e4_3_a_large_file_is_streamed_in_chunks() -> None:
    data = os.urandom(15 * 1024 * 1024)
    with FakeClamd() as server:
        client(server).scan(io.BytesIO(data))
        assert server.sizes == [len(data)]


def test_e4_3_empty_stream_is_scanned() -> None:
    with FakeClamd() as server:
        assert not client(server).scan(io.BytesIO(b"")).infected


def test_e4_3_unreachable_clamd_is_a_retryable_error() -> None:
    with FakeClamd() as server:
        port = server.port
    with pytest.raises(ClamdError, match="cannot reach clamd"):
        ClamdClient("127.0.0.1", port, timeout=1).scan(io.BytesIO(b"x"))
    with pytest.raises(ClamdError):
        ClamdClient("127.0.0.1", port, timeout=1).version()


@pytest.mark.parametrize("behaviour", ["drop", "garbage", "limit"])
def test_e4_3_no_verdict_is_never_a_pass(behaviour: str) -> None:
    with FakeClamd(behaviour=behaviour) as server, pytest.raises(ClamdError):
        client(server).scan(io.BytesIO(os.urandom(300_000)))


def test_e4_3_version_is_parsed_with_its_date() -> None:
    with FakeClamd(version="ClamAV 1.5.4/27800/Wed Oct 14 07:25:00 2026") as server:
        version = client(server).version()
    assert version.signature_date == datetime(2026, 10, 14, 7, 25, tzinfo=UTC)
    assert version.raw == "ClamAV 1.5.4/27800/Wed Oct 14 07:25:00 2026"


@pytest.mark.parametrize("raw", ["ClamAV 1.5.4", "ClamAV 1.5.4/27800/not a date", ""])
def test_e4_3_unreadable_signature_date_is_none(raw: str) -> None:
    assert parse_version(raw).signature_date is None


def test_e4_3_t05_signatures_25_hours_old_are_refused() -> None:
    with FakeClamd(version=version_string(age=timedelta(hours=25))) as server:
        version = client(server).version()
    with pytest.raises(SignatureStaleError):
        require_fresh(version, datetime.now(UTC), timedelta(hours=24))


def test_e4_3_signatures_at_the_limit_pass_and_just_over_fails() -> None:
    version = parse_version("ClamAV 1.5.4/1/Thu Oct 15 00:00:00 2026")
    require_fresh(version, NOW - timedelta(hours=12), timedelta(hours=12))  # exactly 12 h old
    with pytest.raises(SignatureStaleError):
        require_fresh(version, NOW, timedelta(hours=11, minutes=59))
    assert signature_age(version, NOW) == timedelta(hours=12)


def test_e4_3_unknown_signature_date_is_refused_not_trusted() -> None:
    with pytest.raises(SignatureStaleError):
        require_fresh(parse_version("ClamAV 1.5.4"), NOW, timedelta(hours=24))


def version_row(scan_status: str | None) -> DocumentVersion:
    return DocumentVersion(id=uuid.uuid4(), scan_status=scan_status)


def test_e4_3_t06_parse_guard_raises_not_scanned_for_an_unscanned_version() -> None:
    for status in (None, ScanStatus.INFECTED.value, ScanStatus.SCAN_FAILED.value):
        with pytest.raises(ApplicationError) as err:
            require_clean(version_row(status))
        assert err.value.type == "NotScanned" and err.value.non_retryable


def test_e4_3_guard_lets_a_clean_version_through() -> None:
    require_clean(version_row(ScanStatus.CLEAN.value))


def test_e4_3_t07_only_clean_unrejected_versions_are_downloadable() -> None:
    assert is_downloadable("PARSING", "CLEAN") and is_downloadable("PARSED", "CLEAN")
    assert not is_downloadable("REJECTED", "INFECTED")
    assert not is_downloadable("UPLOADED", None)
    assert not is_downloadable("SCANNING", None)
    assert not is_downloadable("SCAN_FAILED", "SCAN_FAILED")
    assert not is_downloadable("DELETED", "CLEAN")


def test_e4_3_scan_outcome_contract() -> None:
    outcome = ScanOutcome(document_version_id=uuid.uuid4(), verdict=ScanStatus.CLEAN)
    assert outcome.signature_version is None
    with pytest.raises(ValueError):
        ScanOutcome(document_version_id=uuid.uuid4(), verdict="MAYBE")  # type: ignore[arg-type]
