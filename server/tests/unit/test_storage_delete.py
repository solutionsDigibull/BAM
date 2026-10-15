"""E4.9 `StorageBackend.delete`, which the purge relies on, on local disk and S3 (moto)."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sbam_api.storage import InvalidStorageKey, Kind, StorageBackend, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.storage.s3 import S3Backend

SCOPE = StorageScope(uuid.uuid4(), uuid.uuid4())
BUCKET = "sbam-delete-test"


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[StorageBackend]:
    if request.param == "local":
        yield LocalDiskBackend(tmp_path)
        return
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Backend(BUCKET, client)


def test_e4_9_delete_removes_the_object(store: StorageBackend) -> None:
    obj = store.put(b"to be purged", SCOPE, Kind.UPLOADS, "application/pdf")
    assert store.exists(obj.storage_key)
    store.delete(obj.storage_key)
    assert not store.exists(obj.storage_key)
    with pytest.raises(FileNotFoundError):
        store.get(obj.storage_key)


def test_e4_9_t09_delete_of_a_missing_object_is_not_an_error(store: StorageBackend) -> None:
    """A purge retried after a partial deletion meets objects that are already gone."""
    obj = store.put(b"twice", SCOPE, Kind.DERIVED, "text/plain")
    store.delete(obj.storage_key)
    store.delete(obj.storage_key)


def test_e4_9_delete_leaves_other_objects_alone(store: StorageBackend) -> None:
    keep = store.put(b"keep", SCOPE, Kind.UPLOADS, "application/pdf")
    gone = store.put(b"gone", SCOPE, Kind.UPLOADS, "application/pdf")
    store.delete(gone.storage_key)
    assert store.get(keep.storage_key) == b"keep"


@pytest.mark.parametrize("key", ["../escape", "/abs/path", "a/../../b", ""])
def test_e4_9_delete_rejects_bad_keys(store: StorageBackend, key: str) -> None:
    with pytest.raises(InvalidStorageKey):
        store.delete(key)
