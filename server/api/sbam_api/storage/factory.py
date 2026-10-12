"""Pick the backend from settings: `STORAGE_BACKEND=local|s3` is the only switch."""

from sbam_api.core.settings import Settings
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.local import LocalDiskBackend


def make_backend(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "s3":
        import boto3

        from sbam_api.storage.s3 import S3Backend

        if not settings.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        endpoint = str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None
        return S3Backend(settings.s3_bucket, boto3.client("s3", endpoint_url=endpoint))
    return LocalDiskBackend(settings.storage_root)
