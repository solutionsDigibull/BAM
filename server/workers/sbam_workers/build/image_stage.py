"""The `build_workflow_image` stage (#867, WG-29): build the Workflow server image and record its
digest.

After the Workflow's server files are stored (#862) the image is built from that exact tar (the
Docker build context: Dockerfile, vendored repository trees, generated skills, `server.py`). The
image digest and the `mcp_server` entry the Workflow manifest will hold (name, version, tools,
digest) are written to the audit trail as one row, `workflow_image_built`, with the bundle hash the
image came from. The row is the manifest's `mcp_server` until Commit (#766) freezes it
(`workflow_image.manifest_with_image` seals it in). The draft row is not rewritten: an `mcp_server`
in it would make the UI_BUNDLE evidence (#857) judge a draft that has no `ui_bundle` yet. The stage
records the content hash the sealed draft would have.

The manifest is not put inside the server bundle: it records the image digest, which the bundle
decides, so it would be circular. The bundle hash stays what the gates and the certification
evidence share; the image is a function of it.

The builder is a port (`image_builder.py`). No docker socket is assumed: with no build host
configured the stage records `workflow_image_failed` (`BUILDER_DISABLED`) and the pipeline goes on,
as it does for a Workflow whose server could not be rendered. A daemon that is away raises and
Temporal retries; a build that fails is recorded with the daemon's last lines. Nothing here changes
a hash that was already frozen.
"""

import asyncio
import hashlib
import io
import json
import tarfile
import uuid
from functools import lru_cache
from typing import Any, Final, NamedTuple, Protocol

from pydantic import ValidationError
from sbam_api.audit import repository as audit
from sbam_api.builds.workflow_image import (
    BUILT_ACTION,
    ENTITY,
    FAILED_ACTION,
    WorkflowImageRecord,
    manifest_with_image,
)
from sbam_api.builds.workflow_server import WorkflowServerRecord
from sbam_api.builds.workflow_server import newest as newest_server
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import WorkflowManifestRepository
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.manifest import McpServerEntry, McpToolEntry
from sbam_contracts.pipeline import IdResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.image_builder import ImageBuilder, ImageBuildError, make_builder
from sbam_workers.build.workflow_server import TOOLS_FILE
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

STAGE: Final = "build_workflow_image"
IDS_MISMATCH: Final = "BuildIdsMismatch"
MAX_FILE_BYTES: Final = 64 * 1024 * 1024


class ImageInput(NamedTuple):
    server: WorkflowServerRecord
    files: dict[str, bytes]  # the Docker build context, checked against the recorded hashes
    version: str
    draft: dict[str, Any] | None  # the newest manifest draft, if one exists


class ImageStore(Protocol):
    def load(self, inp: WorkflowRef) -> ImageInput | ImageBuildError: ...

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None: ...

    def save(self, inp: WorkflowRef, record: WorkflowImageRecord) -> uuid.UUID: ...

    def failed(self, inp: WorkflowRef, error: ImageBuildError) -> uuid.UUID: ...


def _failure(kind: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


def _context(storage: StorageBackend, server: WorkflowServerRecord) -> dict[str, bytes] | None:
    """The stored files, or None when the tar is gone or no longer matches what was recorded."""
    try:
        data = storage.get(server.tar_key)
    except FileNotFoundError:
        return None
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member) if member.isfile() else None
            if handle is None or member.size > MAX_FILE_BYTES:
                return None
            files[member.name] = handle.read()
    sums = {p: hashlib.sha256(d).hexdigest() for p, d in files.items()}
    return files if sums == server.files else None


class SqlImageStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def _ctx(self, inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def load(self, inp: WorkflowRef) -> ImageInput | ImageBuildError:
        """The newest stored server files of this Workflow; another tenant's or project's Workflow
        is not found."""
        ctx = self._ctx(inp)
        with self.sessions() as session:
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            if workflow is None:
                raise _failure(IDS_MISMATCH, f"Workflow {inp.workflow_id} is not in this run")
            server = newest_server(session, inp.tenant_id, inp.workflow_id)
            if server is None:
                return ImageBuildError("SERVER_NOT_STORED", "the server files were not stored")
            drafts = WorkflowManifestRepository(session, ctx).history(inp.workflow_id)
            draft = drafts[0].manifest if drafts else None
            version = workflow.version
        files = _context(self.storage, server)
        if files is None:
            return ImageBuildError("SERVER_FILES_CHANGED", "the stored server files do not match")
        return ImageInput(server, files, version, draft)

    @staticmethod
    def _key(bundle_sha256: str) -> str:
        return f"workflow_image:{bundle_sha256}"

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None:
        with self.sessions() as session:
            row = audit.find_by_key(
                session, inp.tenant_id, ENTITY, str(inp.workflow_id), self._key(bundle_sha256)
            )
            return None if row is None else row.id

    def save(self, inp: WorkflowRef, record: WorkflowImageRecord) -> uuid.UUID:
        with self.sessions() as session:
            row = audit.record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action=BUILT_ACTION,
                entity_type=ENTITY,
                entity_id=str(inp.workflow_id),
                correlation_id=inp.correlation_id,
                after=record.model_dump(mode="json"),
                idempotency_key=self._key(record.bundle_sha256),
            )
            session.commit()
            return row.id

    def failed(self, inp: WorkflowRef, error: ImageBuildError) -> uuid.UUID:
        digest = hashlib.sha256(f"{error.code}:{error}".encode()).hexdigest()[:16]
        key = f"workflow_image_failed:{digest}"
        with self.sessions() as session:
            row = audit.find_by_key(session, inp.tenant_id, ENTITY, str(inp.workflow_id), key)
            if row is None:
                row = audit.record(
                    session,
                    tenant_id=inp.tenant_id,
                    actor_id=SYSTEM_ACTOR_ID,
                    action=FAILED_ACTION,
                    entity_type=ENTITY,
                    entity_id=str(inp.workflow_id),
                    correlation_id=inp.correlation_id,
                    after={"code": error.code, "message": str(error)[:300]},
                    idempotency_key=key,
                )
                session.commit()
            return row.id


def image_tag(server_name: str, bundle_sha256: str) -> str:
    """`wf_<slug>:<12 hex of the bundle hash>`: the same files always get the same tag."""
    return f"{server_name}:{bundle_sha256[:12]}"


def _entry(loaded: ImageInput, digest: str) -> McpServerEntry:
    tools = [McpToolEntry.model_validate(t) for t in json.loads(loaded.files[TOOLS_FILE])]
    return McpServerEntry(
        name=loaded.server.server_name,
        version=loaded.version,
        image_digest=digest,
        tools=tools,
    )


def _sealed_hash(draft: dict[str, Any] | None, record: WorkflowImageRecord) -> str | None:
    if draft is None:
        return None
    try:
        return manifest_with_image(draft, record).integrity.content_sha256
    except (ValidationError, ValueError):
        return None  # a draft the entry does not fit: Commit reports it, the image stays valid


def build_for(store: ImageStore, builder: ImageBuilder, inp: WorkflowRef) -> IdResult:
    """The whole stage, synchronous (tests call it directly; the activity runs it in a thread)."""
    loaded = store.load(inp)
    if isinstance(loaded, ImageBuildError):
        return IdResult(id=store.failed(inp, loaded))
    bundle = loaded.server.bundle_sha256
    found = store.recorded(inp, bundle)
    if found is not None:  # an activity retry, or a rebuild of the same files
        return IdResult(id=found)
    try:
        built = builder.build(loaded.files, image_tag(loaded.server.server_name, bundle))
        entry = _entry(loaded, built.digest)
    except ImageBuildError as exc:
        if exc.retryable:
            raise ApplicationError(str(exc), type=exc.code) from None  # Temporal retries
        return IdResult(id=store.failed(inp, exc))
    except (ValidationError, ValueError, KeyError) as exc:
        return IdResult(id=store.failed(inp, ImageBuildError("ENTRY_INVALID", str(exc)[:300])))
    record = WorkflowImageRecord(
        bundle_sha256=bundle,
        image_digest=built.digest,
        builder=builder.name,
        entry=entry,
    )
    sealed = _sealed_hash(loaded.draft, record)
    if sealed is not None:
        record = record.model_copy(update={"manifest_sha256": sealed})
    return IdResult(id=store.save(inp, record))


@lru_cache(maxsize=1)
def _store() -> SqlImageStore:
    return SqlImageStore(mirror.sessions(), make_backend(WorkerSettings()))  # type: ignore[call-arg]


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def build_workflow_image(inp: WorkflowRef) -> IdResult:
    """Build the Workflow server image from the stored files and record its digest (#867)."""
    settings = WorkerSettings()  # type: ignore[call-arg]
    builder = make_builder(settings.image_builder, settings.image_build_timeout_seconds)
    return await asyncio.to_thread(build_for, _store(), builder, inp)
