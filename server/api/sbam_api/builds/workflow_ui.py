"""The Workflow's generated UI bundle, as the build stored it (#768, ADR 0033).

`build_workflow_ui` renders the bundle (`sbam_workers.build.ui_bundle`), stores it as one
content-addressed tar through `StorageBackend` and writes one audit row (`workflow_ui_built`): the
file map (path to sha256), the template it came from and the `ui_bundle` entry the manifest
records (version, digest, entry file, allowed hosts, workflow server). The manifest draft has no
`mcp_server` until the image is built, and a `ui_bundle` needs one, so the entry lives here until
the Commit step puts both into the frozen manifest. Nothing here writes. A Workflow with no row has
no bundle.
"""

import hashlib
import io
import tarfile
import uuid
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict
from sbam_contracts.canonical import canonical_json
from sbam_contracts.manifest import TemplateRef, UiBundleEntry
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit
from sbam_api.storage.base import StorageBackend

ENTITY: Final = "workflow"
BUILT_ACTION: Final = "workflow_ui_built"
FAILED_ACTION: Final = "workflow_ui_failed"
UI_TAR_TYPE: Final = "application/x-tar"
MAX_FILE_BYTES: Final = 8 * 1024 * 1024


class WorkflowUiRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: UiBundleEntry  # what the manifest's `ui_bundle` will hold
    template: TemplateRef
    tar_key: str  # storage key of the tar holding `files`
    files: dict[str, str]  # path -> sha256 of the file
    copy_source: Literal["model", "manifest"] = "manifest"  # where the screens' text came from

    @property
    def digest(self) -> str:
        return self.entry.digest


def newest(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowUiRecord | None:
    """The latest stored UI bundle of the Workflow (the caller has checked it is in scope)."""
    for row in reversed(audit.entity_history(session, tenant_id, ENTITY, str(workflow_id))):
        if row.action == BUILT_ACTION and row.after is not None:
            return WorkflowUiRecord.model_validate(row.after)
    return None


def packaged_files(storage: StorageBackend, record: WorkflowUiRecord) -> dict[str, bytes] | None:
    """The files of the stored tar, or None when the tar is gone or holds a file the record does
    not list or whose hash differs: a bundle that no longer matches what was recorded."""
    try:
        data = storage.get(record.tar_key)
    except FileNotFoundError:
        return None
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member) if member.isfile() else None
            if handle is None or member.size > MAX_FILE_BYTES:
                return None
            raw = handle.read()
            if hashlib.sha256(raw).hexdigest() != record.files.get(member.name):
                return None
            files[member.name] = raw
    return files if set(files) == set(record.files) else None


def packaged_digest(storage: StorageBackend, record: WorkflowUiRecord) -> str | None:
    """The digest of the bundle as it is packaged now, in the manifest's form (`sha256:` + hash),
    or None when it is not found intact. This is what `check_ui_bundle` compares (UI_BUNDLE)."""
    files = packaged_files(storage, record)
    if files is None:
        return None
    digests = {p: hashlib.sha256(d).hexdigest() for p, d in sorted(files.items())}
    return "sha256:" + hashlib.sha256(canonical_json(digests)).hexdigest()
