"""The Workflow server's built image, as the build recorded it (#867, ADR 0028 decision 3).

`build_workflow_image` builds one image from the stored server files (#862) and writes one audit
row (`workflow_image_built`): the image digest, the bundle hash it was built from and the
`mcp_server` entry (name, version, tools, digest) the Workflow manifest records. Like the UI bundle
entry (#768) it lives on the audit trail until the Commit step (#766) puts it into the manifest it
freezes; `manifest_with_image` is that merge (`with_server`: the entry is under the content hash,
so the manifest is sealed again). The draft row, and every manifest already frozen, are never
rewritten, so no existing hash changes.

A digest belongs to the bundle it was built from: `current` returns the row only while it still
matches the newest stored server, so a stale image is never started or frozen. Nothing here writes.
"""

import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sbam_contracts.manifest import McpServerEntry, WorkflowManifest
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit
from sbam_api.builds.workflow_server import newest as newest_server

ENTITY: Final = "workflow"
BUILT_ACTION: Final = "workflow_image_built"
FAILED_ACTION: Final = "workflow_image_failed"


class WorkflowImageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_sha256: str  # the stored server files (the Docker build context) it was built from
    image_digest: str  # `sha256:<64 hex>`, the image id of the built image
    builder: str  # which builder made it (`docker`, ...)
    entry: McpServerEntry  # what the manifest's `mcp_server` holds
    manifest_sha256: str | None = None  # content hash of the sealed draft with `entry`, if a draft


def newest(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowImageRecord | None:
    """The latest built image of the Workflow (the caller has checked it is in scope)."""
    for row in reversed(audit.entity_history(session, tenant_id, ENTITY, str(workflow_id))):
        if row.action == BUILT_ACTION and row.after is not None:
            return WorkflowImageRecord.model_validate(row.after)
    return None


def current(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowImageRecord | None:
    """The built image, only while it was built from the newest stored server files."""
    server = newest_server(session, tenant_id, workflow_id)
    image = newest(session, tenant_id, workflow_id)
    if server is None or image is None or image.bundle_sha256 != server.bundle_sha256:
        return None
    return image


def manifest_with_image(manifest: dict[str, Any], record: WorkflowImageRecord) -> WorkflowManifest:
    """The Workflow manifest with the built server recorded and the content hash sealed again."""
    return WorkflowManifest.sealed({**manifest, "mcp_server": record.entry.dump()})
