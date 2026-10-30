"""S3 backend stub (C-37: local disk now, AWS S3 later). Same keys and interface as local disk."""

import hashlib
import io
import tempfile
from collections.abc import Iterator
from typing import Any, BinaryIO

from botocore.exceptions import BotoCoreError, ClientError

from sbam_api.storage.base import (
    InvalidStorageKey,
    Kind,
    ListedObject,
    StorageIntegrityError,
    StorageScope,
    StorageUnavailable,
    StoredObject,
    hash_into,
    parse_key,
)


class S3Backend:
    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self.s3 = client

    def put(
        self,
        data: BinaryIO | bytes,
        scope: StorageScope,
        kind: Kind,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StoredObject:
        # The key is the hash, so spool and hash before uploading.
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
            sha256, size = hash_into(data, spool, max_bytes)
            key = scope.key(kind, sha256)
            try:
                if not self.exists(key):
                    spool.seek(0)
                    self.s3.upload_fileobj(spool, self.bucket, key)
                else:  # in use again: refresh LastModified so the unreferenced-file sweep skips it
                    self.s3.copy_object(
                        Bucket=self.bucket,
                        Key=key,
                        CopySource={"Bucket": self.bucket, "Key": key},
                        MetadataDirective="REPLACE",
                    )
            except (BotoCoreError, ClientError):
                raise StorageUnavailable() from None
        return StoredObject(key, sha256, size, content_type)

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(self.get(key))

    def get(self, key: str, *, verify: bool = False) -> bytes:
        parse_key(key)
        try:
            body: bytes = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from None
            raise StorageUnavailable() from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        if verify and hashlib.sha256(body).hexdigest() != parse_key(key)[1]:
            raise StorageIntegrityError()
        return body

    def delete(self, key: str) -> None:
        parse_key(key)
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)  # no error when the key is absent
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None

    def exists(self, key: str) -> bool:
        parse_key(key)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageUnavailable() from None
        except BotoCoreError:
            raise StorageUnavailable() from None
        return True

    def list_keys(self, prefix: str) -> Iterator[ListedObject]:
        try:
            for page in self.s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            ):
                for item in page.get("Contents", []):
                    try:
                        parse_key(item["Key"])
                    except InvalidStorageKey:
                        continue
                    yield ListedObject(item["Key"], int(item["Size"]), item["LastModified"])
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None
