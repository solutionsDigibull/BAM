"""E1.6 storage: one interface suite run against LocalDiskBackend and the S3 stub (moto)."""

import errno
import hashlib
import io
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sbam_api.storage import (
    InvalidStorageKey,
    Kind,
    ObjectTooLarge,
    StorageBackend,
    StorageIntegrityError,
    StorageScope,
    StorageUnavailable,
)
from sbam_api.storage.local import STALE_TMP_SECONDS, LocalDiskBackend
from sbam_api.storage.s3 import S3Backend

SCOPE = StorageScope(uuid.uuid4(), uuid.uuid4())
BUCKET = "sbam-test"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """A short root: a workflow-scoped key is ~180 chars, and pytest's own tmp_path can push it past
    Windows' 260-char MAX_PATH. Production runs on Linux (`/data/storage`)."""
    root = Path(tempfile.mkdtemp(prefix="st"))
    yield root

    def writable(func: object, path: str, _: object) -> None:
        os.chmod(path, stat.S_IWRITE)  # objects are 0444
        os.unlink(path)

    shutil.rmtree(root, onexc=writable)


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[StorageBackend]:
    if request.param == "local":
        yield LocalDiskBackend(tmp_path)
        return
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Backend(BUCKET, client)


def test_e1_6_t01_put_get_round_trip(store: StorageBackend) -> None:
    data = os.urandom(2 * 1024 * 1024)
    obj = store.put(io.BytesIO(data), SCOPE, Kind.UPLOADS, "application/pdf")
    assert obj.sha256 == hashlib.sha256(data).hexdigest()
    assert obj.storage_key == f"{SCOPE.tenant_id}/{SCOPE.project_id}/uploads/{obj.sha256}"
    assert obj.size == 2_097_152
    assert store.get(obj.storage_key, verify=True) == data
    assert store.exists(obj.storage_key)


def test_e1_6_workflow_scope_and_zero_byte_object(store: StorageBackend) -> None:
    scope = StorageScope(SCOPE.tenant_id, SCOPE.project_id, uuid.uuid4())
    obj = store.put(b"", scope, Kind.CODE, "text/plain")
    assert obj.size == 0
    assert obj.storage_key.split("/")[2:4] == [str(scope.workflow_id), "code"]
    assert store.get(obj.storage_key, verify=True) == b""


@pytest.mark.parametrize(
    "key",
    [
        f"{SCOPE.tenant_id}/{SCOPE.project_id}/uploads/../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        f"{SCOPE.tenant_id}\\{SCOPE.project_id}\\uploads\\{'a' * 64}",
        f"{SCOPE.tenant_id}/{SCOPE.project_id}/secrets/{'a' * 64}",  # unknown kind
        f"{SCOPE.tenant_id}/{SCOPE.project_id}/uploads/report.pdf",  # a filename, not a hash
    ],
)
def test_e1_6_t02_bad_keys_are_rejected_before_any_read(store: StorageBackend, key: str) -> None:
    for call in (store.get, store.open, store.exists):
        with pytest.raises(InvalidStorageKey):
            call(key)


def test_e1_6_t04_same_content_twice_is_one_object(store: StorageBackend, tmp_path: Path) -> None:
    first = store.put(b"same bytes", SCOPE, Kind.DERIVED, "text/plain")
    second = store.put(io.BytesIO(b"same bytes"), SCOPE, Kind.DERIVED, "text/plain")
    assert first.storage_key == second.storage_key
    if isinstance(store, LocalDiskBackend):
        assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1  # no tmp files left


def test_e1_6_t09_corrupted_bytes_fail_verification(store: StorageBackend) -> None:
    obj = store.put(b"original", SCOPE, Kind.OKF, "application/json")
    if isinstance(store, LocalDiskBackend):
        path = store.root / obj.storage_key
        path.chmod(0o644)
        path.write_bytes(b"tampered")
    else:
        assert isinstance(store, S3Backend)
        store.s3.put_object(Bucket=BUCKET, Key=obj.storage_key, Body=b"tampered")
    with pytest.raises(StorageIntegrityError) as err:
        store.get(obj.storage_key, verify=True)
    assert err.value.code == "HASH_MISMATCH"


def test_e1_6_max_bytes_rejects_and_stores_nothing(store: StorageBackend) -> None:
    with pytest.raises(ObjectTooLarge) as err:
        store.put(b"x" * 11, SCOPE, Kind.UPLOADS, "text/plain", max_bytes=10)
    assert err.value.status == 413
    key = SCOPE.key(Kind.UPLOADS, hashlib.sha256(b"x" * 11).hexdigest())
    assert not store.exists(key)


def test_e1_6_missing_object_is_file_not_found(store: StorageBackend) -> None:
    with pytest.raises(FileNotFoundError):
        store.get(SCOPE.key(Kind.UPLOADS, "0" * 64))


# --- Local disk specifics ----------------------------------------------------------------------


def test_e1_6_t03_crash_before_rename_leaves_no_object_and_tmp_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalDiskBackend(tmp_path)

    def crash(*_: object) -> None:
        raise KeyboardInterrupt  # the process dies between the tmp write and the rename

    monkeypatch.setattr("sbam_api.storage.local.os.replace", crash)
    with pytest.raises(KeyboardInterrupt):
        store.put(b"half written", SCOPE, Kind.UPLOADS, "text/plain")
    monkeypatch.undo()
    key = SCOPE.key(Kind.UPLOADS, hashlib.sha256(b"half written").hexdigest())
    assert not store.exists(key)
    # A real crash leaves the tmp file behind; the next start removes it once it is stale.
    leftover = store.tmp / "crashed"
    leftover.write_bytes(b"half written")
    old = time.time() - STALE_TMP_SECONDS - 1
    os.utime(leftover, (old, old))
    in_flight = store.tmp / "in-flight"
    in_flight.write_bytes(b"another writer")
    LocalDiskBackend(tmp_path)
    assert not leftover.exists()
    assert in_flight.exists()  # a recent tmp may belong to a concurrent writer


def test_e1_6_t08_disk_full_raises_unavailable_and_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalDiskBackend(tmp_path)

    def full(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("sbam_api.storage.local.os.fsync", full)
    with pytest.raises(StorageUnavailable) as err:
        store.put(b"x" * 4096, SCOPE, Kind.UPLOADS, "text/plain")
    assert err.value.status == 503
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_e1_6_concurrent_writers_of_the_same_content(tmp_path: Path) -> None:
    store = LocalDiskBackend(tmp_path)
    data = os.urandom(256 * 1024)
    keys: list[str] = []
    threads = [
        threading.Thread(
            target=lambda: keys.append(store.put(data, SCOPE, Kind.UPLOADS, "x").storage_key)
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(keys)) == 1 and len(keys) == 8
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == [tmp_path / keys[0]]
    assert store.get(keys[0], verify=True) == data


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_e1_6_objects_are_read_only(tmp_path: Path) -> None:
    store = LocalDiskBackend(tmp_path)
    obj = store.put(b"frozen", SCOPE, Kind.EXPORTS, "application/zip")
    assert (store.root / obj.storage_key).stat().st_mode & 0o777 == 0o444
