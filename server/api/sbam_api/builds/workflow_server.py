"""The Workflow's generated server files, as the build stored them (#862, ADR 0028 decision 3).

`build_workflow_server` renders the aggregate server and the files around it (`server.py`,
`tools.json`, the Dockerfile, the loop specs, `schema.sql` and each Sub-Skill's generated files),
stores them as one content-addressed tar through `StorageBackend` and writes one audit row
(`workflow_server_built`) with the file map (path to sha256) and the bundle hash. The code preview
(#763) lists the files from that row with the `generated` tag and reads their bytes from the tar,
checked against the recorded hash. Nothing here writes. A Workflow with no row shows no
server files.
"""

import hashlib
import io
import tarfile
import uuid
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit
from sbam_api.core.conventions import not_found
from sbam_api.storage.base import StorageBackend

ENTITY: Final = "workflow"
BUILT_ACTION: Final = "workflow_server_built"
FAILED_ACTION: Final = "workflow_server_failed"
SERVER_TAR_TYPE: Final = "application/x-tar"


class WorkflowServerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_name: str
    bundle_sha256: str  # the whole Docker build context, the hash the gates and evidence share
    tar_key: str  # storage key of the tar holding `files`
    files: dict[str, str]  # path -> sha256 of the file
    tools: list[str]
    base_layer_key: str
    top_layer_key: str


def newest(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowServerRecord | None:
    """The latest stored server of the Workflow (the caller has checked it is in scope)."""
    rows = audit.entity_history(session, tenant_id, ENTITY, str(workflow_id))
    for row in reversed(rows):
        if row.action == BUILT_ACTION and row.after is not None:
            return WorkflowServerRecord.model_validate(row.after)
    return None


def read_file(storage: StorageBackend, record: WorkflowServerRecord, path: str) -> bytes:
    """The bytes of `path`; 404 unless the stored file map lists it or the tar no longer matches."""
    if path not in record.files:
        raise not_found()
    try:
        data = storage.get(record.tar_key)
    except FileNotFoundError:
        raise not_found() from None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        try:
            member = tar.getmember(path)
        except KeyError:
            raise not_found() from None
        handle = tar.extractfile(member) if member.isfile() else None
        if handle is None:
            raise not_found()
        raw = handle.read()
    if hashlib.sha256(raw).hexdigest() != record.files[path]:
        raise not_found()
    return raw
