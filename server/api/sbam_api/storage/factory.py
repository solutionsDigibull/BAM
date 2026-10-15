"""Pick the backend from settings: `STORAGE_BACKEND=local|s3` is the only switch."""

from pathlib import Path
from typing import Literal, Protocol

from sbam_api.storage.base import StorageBackend
from sbam_api.storage.local import LocalDiskBackend


class StorageConfig(Protocol):
    """The API's `Settings` and the workers' `WorkerSettings` both satisfy this (read-only)."""

    @property
    def storage_backend(self) -> Literal["local", "s3"]: ...

    @property
    def storage_root(self) -> Path | None: ...

    @property
    def s3_bucket(self) -> str | None: ...

    @property
    def s3_endpoint_url(self) -> object | None: ...


def make_backend(settings: StorageConfig) -> StorageBackend:
    if settings.storage_backend == "s3":
        import boto3

        from sbam_api.storage.s3 import S3Backend

        if not settings.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        endpoint = str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None
        return S3Backend(settings.s3_bucket, boto3.client("s3", endpoint_url=endpoint))
    if settings.storage_root is None:
        raise ValueError("STORAGE_BACKEND=local needs STORAGE_ROOT")
    return LocalDiskBackend(settings.storage_root)
