"""The `build_workflow_server` stage (#862, WG-28): store the Workflow's generated server files.

After every Sub-Skill is built, the Workflow's MCP server is rendered by
`workflow_server.build_bundle` (#758) from the Skill manifests and the Sub-Skills' assembled
bundles: `server.py`, `tools.json`, the Dockerfile, the loop specs, `schema.sql` and each
Sub-Skill's generated files. This stage
stores them as one content-addressed tar through `StorageBackend` (indexed as an artifact) and
writes the `workflow_server_built` audit row: the file map (path to sha256), the bundle hash the
gates and the certification evidence share, and the layer keys. The code preview (#763, #841) and
the Staging runtime (#765) read it from there.

The Workflow manifest is not part of the bundle (it records the server entry once the image is
built, so it would be circular); the preview lists it from its own row. The vendored repository
trees (#867) are: `build.repos` of the Skill manifests names each one, the fetcher's store holds it
(`Kind.REPOS`, found through the scan report), and it is copied untouched under
`vendor/<repo>@<commit>/` into the Docker build context, so the Dockerfile's `COPY vendor/` layer
exists and the gates and the preview see the same files the image ships. A tree whose digest is not
the one the manifest recorded, or that is gone, is a `VENDOR_*` failure, never a silent skip.

A Workflow whose bundle cannot be rendered (a Sub-Skill without its files, an unpinned image) is
recorded as `workflow_server_failed` with the stable error code and the pipeline goes on: nothing
here changes a hash that was already frozen. Storage trouble raises and Temporal retries it.
"""

import asyncio
import hashlib
import io
import tarfile
import uuid
from functools import lru_cache
from typing import Final, Protocol

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.audit import repository as audit
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.builds.workflow_code import overlays_of
from sbam_api.builds.workflow_server import (
    BUILT_ACTION,
    ENTITY,
    FAILED_ACTION,
    SERVER_TAR_TYPE,
    WorkflowServerRecord,
)
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.repository import SubSkillManifestRepository
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StoredObject
from sbam_api.storage.factory import make_backend
from sbam_api.workflows.repository import WorkflowRepository
from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.pipeline import IdResult, WorkflowRef
from sbam_contracts.repo_overlay import RepoOverlay
from sbam_contracts.repo_scan import RepoScanReport
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sandbox import normalize_digest
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.build.repo_attach import BUNDLE_MISSING, read_tree
from sbam_workers.build.workflow_server import (
    SkillPart,
    WorkflowServerError,
    WorkflowServerInput,
    build_bundle,
)
from sbam_workers.registry import activity_for
from sbam_workers.repo_fetch.overlay import OverlayError, build_overlay, tree_digest
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.settings import WorkerSettings

STAGE: Final = "build_workflow_server"
IDS_MISMATCH: Final = "BuildIdsMismatch"
MAX_SCHEMA_BYTES: Final = 1024 * 1024


def _failure(kind: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


def vendored_tree(
    storage: StorageBackend, scope: StorageScope, overlay: RepoOverlay
) -> dict[str, bytes]:
    """`vendor/<repo>@<commit>/...` of one attached repository, read from the fetcher's store.

    The tree is found through the scan report the overlay names, and must be the tree the manifest
    recorded (its digest) with no patch the build did not keep: a patch file is stored nowhere yet,
    so an overlay that lists one cannot be reproduced and is refused."""
    if overlay.patches:
        raise WorkflowServerError("VENDOR_PATCHES_UNAVAILABLE", f"{overlay.repo_dir} lists patches")
    if overlay.scan_report_sha256 is None:
        raise WorkflowServerError("VENDOR_REPORT_MISSING", f"{overlay.repo_dir} has no scan report")
    try:
        report = RepoScanReport.model_validate_json(
            storage.get(scope.key(Kind.REPOS, overlay.scan_report_sha256))
        )
        if report.bundle_sha256 is None:
            raise WorkflowServerError("VENDOR_TREE_MISSING", f"{overlay.repo_dir} was not vendored")
        tree = read_tree(storage, scope, report.bundle_sha256)
    except FileNotFoundError:
        raise WorkflowServerError(
            "VENDOR_TREE_MISSING", f"the scan report of {overlay.repo_dir} is gone"
        ) from None
    except ApplicationError as exc:
        if exc.type == BUNDLE_MISSING:
            raise WorkflowServerError(
                "VENDOR_TREE_MISSING", f"the stored tree of {overlay.repo_dir} is gone"
            ) from None
        raise
    if tree_digest(tree) != overlay.vendor_tree_sha256:
        raise WorkflowServerError(
            "VENDOR_TREE_CHANGED", f"{overlay.repo_dir} is not the tree the manifest recorded"
        )
    try:
        built = build_overlay(url=str(overlay.url), commit=str(overlay.commit), vendor=tree)
    except OverlayError as exc:
        raise WorkflowServerError("VENDOR_TREE_UNSAFE", str(exc)) from None
    prefix = f"{overlay.repo_dir}/"
    return {p: d for p, d in built.layout.items() if p.startswith(prefix)}


def _read_tar(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member) if member.isfile() else None
            if handle is not None:
                files[member.name] = handle.read()
    return files


class ServerStore(Protocol):
    storage: StorageBackend

    def load(self, inp: WorkflowRef) -> WorkflowServerInput: ...

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None: ...

    def save(
        self, inp: WorkflowRef, record: WorkflowServerRecord, stored: StoredObject
    ) -> uuid.UUID: ...

    def failed(self, inp: WorkflowRef, error: WorkflowServerError) -> uuid.UUID: ...


class SqlServerStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def _ctx(self, inp: WorkflowRef) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def load(self, inp: WorkflowRef) -> WorkflowServerInput:
        """The server's inputs; another tenant's or project's Workflow is not found."""
        ctx = self._ctx(inp)
        with self.sessions() as session:
            workflow = WorkflowRepository(session, ctx).get(inp.workflow_id)
            if workflow is None:
                raise _failure(IDS_MISMATCH, f"Workflow {inp.workflow_id} is not in this run")
            subs = SubSkillRepository(session, ctx).of_workflow(inp.workflow_id)
            manifests = SubSkillManifestRepository(session, ctx).newest([s.id for s in subs])
            attempts = CodeAttemptRepository(session, ctx)
            parts: list[SkillPart] = []
            digests: set[str] = set()
            vendor: dict[str, bytes] = {}
            seen: set[str] = set()
            scope = StorageScope(inp.tenant_id, inp.project_id)
            for sub in subs:
                found = manifests.get(sub.id)
                rows = [
                    a
                    for a in attempts.of_sub_skill(inp.workflow_id, sub.id)
                    if a.status == CodeAttemptStatus.ASSEMBLED.value and a.bundle_key
                ]
                if found is None or not rows:
                    raise WorkflowServerError(
                        "SKILL_NOT_BUILT", f"{sub.skill_id} has no manifest or assembled bundle"
                    )
                digests.add(found.runtime_digest)
                for overlay in overlays_of(found.manifest):
                    if overlay.repo_dir not in seen:  # one copy, however many Sub-Skills use it
                        seen.add(overlay.repo_dir)
                        vendor.update(vendored_tree(self.storage, scope, overlay))
                key = rows[-1].bundle_key or ""
                parts.append(
                    SkillPart(SkillManifest.model_validate(found.manifest), self._tar(key))
                )
            if len(digests) != 1:
                raise WorkflowServerError(
                    "RUNTIME_MISMATCH", "the Sub-Skills were built on different runtime images"
                )
            schema = WorkflowSchemaRepository(session, ctx).latest(
                inp.workflow_id, status=GateStatus.PASSED
            )
            schema_sql = ""
            if schema is not None and schema.storage_key:
                with self.storage.open(schema.storage_key) as stream:
                    schema_sql = stream.read(MAX_SCHEMA_BYTES).decode("utf-8", "replace")
            return WorkflowServerInput(
                workflow_name=workflow.name,
                skills=parts,
                schema_sql=schema_sql,
                base_image=f"skill-runtime@{normalize_digest(digests.pop())}",
                vendor=vendor or None,
            )

    def _tar(self, key: str) -> dict[str, bytes]:
        return _read_tar(self.storage.get(key))

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None:
        with self.sessions() as session:
            row = audit.find_by_key(
                session, inp.tenant_id, ENTITY, str(inp.workflow_id), self._key(bundle_sha256)
            )
            return None if row is None else row.id

    @staticmethod
    def _key(bundle_sha256: str) -> str:
        return f"workflow_server:{bundle_sha256}"

    def save(
        self, inp: WorkflowRef, record: WorkflowServerRecord, stored: StoredObject
    ) -> uuid.UUID:
        with self.sessions() as session:
            ArtifactRepository(session, self._ctx(inp)).index(
                ArtifactKind.CODE_BUNDLE, stored, "sbam-worker"
            )
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

    def failed(self, inp: WorkflowRef, error: WorkflowServerError) -> uuid.UUID:
        digest = hashlib.sha256(f"{error.code}:{error}".encode()).hexdigest()[:16]
        with self.sessions() as session:
            row = audit.find_by_key(
                session,
                inp.tenant_id,
                ENTITY,
                str(inp.workflow_id),
                f"workflow_server_failed:{digest}",
            )
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
                    idempotency_key=f"workflow_server_failed:{digest}",
                )
                session.commit()
            return row.id


def build_for(store: ServerStore, inp: WorkflowRef) -> IdResult:
    """The whole stage, synchronous (tests call it directly; the activity runs it in a thread)."""
    try:
        bundle = build_bundle(store.load(inp))
    except WorkflowServerError as exc:
        return IdResult(id=store.failed(inp, exc))
    found = store.recorded(inp, bundle.bundle_sha256)
    if found is not None:  # an activity retry, or a rebuild that gave the same bytes
        return IdResult(id=found)
    stored = store.storage.put(
        bundle_tar(bundle.files),
        StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id),
        Kind.CODE,
        SERVER_TAR_TYPE,
    )
    record = WorkflowServerRecord(
        server_name=bundle.server_name,
        bundle_sha256=bundle.bundle_sha256,
        tar_key=stored.storage_key,
        files={p: hashlib.sha256(d).hexdigest() for p, d in bundle.files.items()},
        tools=[t.name for t in bundle.tools],
        base_layer_key=bundle.base_layer_key,
        top_layer_key=bundle.top_layer_key,
    )
    return IdResult(id=store.save(inp, record, stored))


@lru_cache(maxsize=1)
def _store() -> SqlServerStore:
    return SqlServerStore(mirror.sessions(), make_backend(WorkerSettings()))  # type: ignore[call-arg]


@activity_for(TaskQueue.BUILD)
@activity.defn(name=STAGE)
async def build_workflow_server(inp: WorkflowRef) -> IdResult:
    """Render and store the Workflow's server files and record their map (#862)."""
    return await asyncio.to_thread(build_for, _store(), inp)
