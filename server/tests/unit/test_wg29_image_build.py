"""WG-29 (#867): vendored trees in the server image, the image build and its digest in the manifest.

- WG29-T01: an attached repository's tree is copied untouched into the Docker build context and the
  Dockerfile has its `COPY vendor/` layer; a build without a repository has neither.
- WG29-T02: a tree that changed, is gone, or lists patches nobody stored is refused with a code.
- WG29-T03: the image is built from the stored files, its digest and `mcp_server` entry recorded
  once (a retry is the same row) with the sealed manifest hash; frozen hashes are untouched.
- WG29-T04: no build host, a failed build, a stale or tampered context are recorded, not raised; a
  daemon that is away raises so Temporal retries.
- WG29-T05: the docker builder runs one `docker build` with no shell and reads the image id.
"""

import hashlib
import json
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import record as audit_record
from sbam_api.builds.workflow_image import WorkflowImageRecord, current, manifest_with_image
from sbam_api.builds.workflow_server import BUILT_ACTION as SERVER_BUILT
from sbam_api.builds.workflow_server import WorkflowServerRecord
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.pipeline import WorkflowRef
from sbam_contracts.repo_overlay import RepoOverlay
from sbam_workers.build.image_builder import (
    BuiltImage,
    DisabledBuilder,
    DockerCliBuilder,
    ImageBuildError,
    make_builder,
)
from sbam_workers.build.image_stage import (
    ImageInput,
    SqlImageStore,
    _context,
    build_for,
    image_tag,
)
from sbam_workers.build.repo_attach import AttachRepoInput, attach_for
from sbam_workers.build.server_stage import vendored_tree
from sbam_workers.build.workflow_server import WorkflowServerError, build_bundle
from sbam_workers.repo_fetch.report import bundle_tar
from temporalio.exceptions import ApplicationError
from test_manifest import workflow
from test_wg10_workflow_server import ms01_files, request, template  # noqa: F401 - fixtures
from test_wg12_workflow_registry import PROJECT as WF_PROJECT
from test_wg12_workflow_registry import TENANT as WF_TENANT
from test_wg12_workflow_registry import WORKFLOW_ID, World
from test_wg28_attach_and_server import COMMIT, PROJECT, TENANT, Fetcher, MemoryAttachStore, NoClam

DIGEST = "sha256:" + "d" * 64


def attached_overlay(storage: LocalDiskBackend) -> RepoOverlay:
    """The overlay record `attach_repo` writes for a vendored repository (a real stored tree)."""
    store = MemoryAttachStore()
    inp = AttachRepoInput(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg29",
        workflow_id=uuid.uuid4(),
        sub_skill_id=uuid.uuid4(),
        build_no=1,
        url="https://github.com/acme/lib",
    )
    attach_for(inp, storage, NoClam(), store, Fetcher(True))
    [row] = store.rows.values()
    assert row.overlay is not None
    return RepoOverlay.model_validate(row.overlay)


def scope() -> StorageScope:
    return StorageScope(TENANT, PROJECT)


# --- WG29-T01 ------------------------------------------------------------------------------------


def test_wg29_t01_the_attached_tree_goes_into_the_build_context_untouched(tmp_path: Path) -> None:
    storage = LocalDiskBackend(tmp_path)
    overlay = attached_overlay(storage)
    tree = vendored_tree(storage, scope(), overlay)
    assert tree == {
        f"vendor/lib@{COMMIT}/LICENSE": b"MIT\n",
        f"vendor/lib@{COMMIT}/lib.py": b"X = 1\n",
    }


def test_wg29_t01_the_dockerfile_copies_vendor_only_when_a_tree_is_there(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    storage = LocalDiskBackend(tmp_path)
    vendor = vendored_tree(storage, scope(), attached_overlay(storage))
    with_repo = build_bundle(request(ms01_files, vendor=vendor))
    without = build_bundle(request(ms01_files, vendor=None))
    assert b"COPY vendor/ ./vendor/" in with_repo.files["Dockerfile"]
    assert f"vendor/lib@{COMMIT}/lib.py" in with_repo.files
    assert b"vendor/" not in without.files["Dockerfile"]  # a build without a repository works
    assert not any(p.startswith("vendor/") for p in without.files)
    assert with_repo.bundle_sha256 != without.bundle_sha256  # the vendored files are in the hash
    assert with_repo.base_layer_key != without.base_layer_key  # vendored code is the base layer
    assert with_repo.top_layer_key == without.top_layer_key  # a vendored tree rebuilds no skill


# --- WG29-T02 ------------------------------------------------------------------------------------


def test_wg29_t02_a_tree_that_changed_or_is_gone_is_refused(tmp_path: Path) -> None:
    storage = LocalDiskBackend(tmp_path)
    overlay = attached_overlay(storage)
    changed = overlay.model_copy(update={"vendor_tree_sha256": "f" * 64})
    with pytest.raises(WorkflowServerError) as err:
        vendored_tree(storage, scope(), changed)
    assert err.value.code == "VENDOR_TREE_CHANGED"
    gone = overlay.model_copy(update={"scan_report_sha256": "e" * 64})
    with pytest.raises(WorkflowServerError) as missing:
        vendored_tree(storage, scope(), gone)
    assert missing.value.code == "VENDOR_TREE_MISSING"
    none = overlay.model_copy(update={"scan_report_sha256": None})
    with pytest.raises(WorkflowServerError) as report:
        vendored_tree(storage, scope(), none)
    assert report.value.code == "VENDOR_REPORT_MISSING"


def test_wg29_t02_a_tree_of_another_tenant_is_not_found(tmp_path: Path) -> None:
    storage = LocalDiskBackend(tmp_path)
    overlay = attached_overlay(storage)
    other = StorageScope(uuid.uuid4(), PROJECT)
    with pytest.raises(WorkflowServerError) as err:
        vendored_tree(storage, other, overlay)
    assert err.value.code == "VENDOR_TREE_MISSING"


def test_wg29_t02_patches_nobody_stored_are_refused(tmp_path: Path) -> None:
    storage = LocalDiskBackend(tmp_path)
    overlay = attached_overlay(storage).model_dump(mode="json")
    overlay["patches"] = [
        {
            "path": "patches/lib@c/0001-x.patch",
            "sha256": "a" * 64,
            "reason": "r",
            "files": ["lib.py"],
        }
    ]
    with pytest.raises(WorkflowServerError) as err:
        vendored_tree(storage, scope(), RepoOverlay.model_validate(overlay))
    assert err.value.code == "VENDOR_PATCHES_UNAVAILABLE"


# --- WG29-T03 ------------------------------------------------------------------------------------


class FakeBuilder:
    name = "fake"

    def __init__(self, digest: str = DIGEST, error: ImageBuildError | None = None) -> None:
        self.digest, self.error = digest, error
        self.calls: list[tuple[dict[str, bytes], str]] = []

    def build(self, files: Mapping[str, bytes], tag: str) -> BuiltImage:
        self.calls.append((dict(files), tag))
        if self.error is not None:
            raise self.error
        return BuiltImage(self.digest)


class MemoryImageStore:
    def __init__(self, loaded: ImageInput | ImageBuildError) -> None:
        self.loaded = loaded
        self.saved: list[WorkflowImageRecord] = []
        self.failures: list[ImageBuildError] = []

    def load(self, inp: WorkflowRef) -> ImageInput | ImageBuildError:
        return self.loaded

    def recorded(self, inp: WorkflowRef, bundle_sha256: str) -> uuid.UUID | None:
        for n, row in enumerate(self.saved):
            if row.bundle_sha256 == bundle_sha256:
                return uuid.UUID(int=n + 1)
        return None

    def save(self, inp: WorkflowRef, record: WorkflowImageRecord) -> uuid.UUID:
        self.saved.append(record)
        return uuid.UUID(int=len(self.saved))

    def failed(self, inp: WorkflowRef, error: ImageBuildError) -> uuid.UUID:
        self.failures.append(error)
        return uuid.uuid4()


def ref() -> WorkflowRef:
    return WorkflowRef(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg29",
        workflow_id=uuid.uuid4(),
    )


def loaded(
    ms01_files: dict[str, bytes], draft: dict[str, Any] | None = None  # noqa: F811
) -> tuple[ImageInput, str]:
    bundle = build_bundle(request(ms01_files))
    server = WorkflowServerRecord(
        server_name=bundle.server_name,
        bundle_sha256=bundle.bundle_sha256,
        tar_key="k",
        files={p: hashlib.sha256(d).hexdigest() for p, d in bundle.files.items()},
        tools=[t.name for t in bundle.tools],
        base_layer_key=bundle.base_layer_key,
        top_layer_key=bundle.top_layer_key,
    )
    return ImageInput(server, dict(bundle.files), "1.0.0", draft), bundle.bundle_sha256


def test_wg29_t03_the_digest_and_entry_are_recorded_once(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    inp, bundle = loaded(ms01_files)
    store, builder = MemoryImageStore(inp), FakeBuilder()
    first = build_for(store, builder, ref())
    again = build_for(store, builder, ref())
    assert first == again and len(store.saved) == 1 and len(builder.calls) == 1
    [record] = store.saved
    assert record.image_digest == DIGEST and record.bundle_sha256 == bundle
    assert record.builder == "fake" and record.manifest_sha256 is None  # no draft yet
    assert record.entry.image_digest == DIGEST and record.entry.version == "1.0.0"
    assert [t.name for t in record.entry.tools] == inp.server.tools
    files, tag = builder.calls[0]
    assert files == inp.files and tag == image_tag("wf_investor_onboarding", bundle)


def test_wg29_t03_the_sealed_manifest_carries_the_digest_and_the_draft_is_untouched(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    draft = WorkflowManifest.sealed(workflow()).dump()
    before = json.dumps(draft, sort_keys=True)
    inp, _ = loaded(ms01_files, draft)
    store = MemoryImageStore(inp)
    build_for(store, FakeBuilder(), ref())
    [record] = store.saved
    sealed = manifest_with_image(draft, record)
    assert sealed.dump()["mcp_server"]["image_digest"] == DIGEST
    assert record.manifest_sha256 == sealed.integrity.content_sha256
    assert sealed.integrity.content_sha256 != draft["integrity"]["content_sha256"]
    assert json.dumps(draft, sort_keys=True) == before  # existing manifests keep their hash


def test_wg29_t03_the_manifest_is_not_inside_the_server_bundle(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    """It records the digest the bundle decides: circular, so it stays out (ADR 0028)."""
    inp, _ = loaded(ms01_files)
    assert "manifest.json" not in inp.files


def test_wg29_t03_the_stored_tar_is_the_context_and_a_changed_file_is_not(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    inp, _ = loaded(ms01_files)
    storage = LocalDiskBackend(tmp_path)
    ok = storage.put(
        bundle_tar(inp.files), StorageScope(TENANT, PROJECT), Kind.CODE, "application/x-tar"
    )
    server = inp.server.model_copy(update={"tar_key": ok.storage_key})
    assert _context(storage, server) == inp.files
    changed = storage.put(
        bundle_tar({**inp.files, "server.py": b"evil\n"}),
        StorageScope(TENANT, PROJECT),
        Kind.CODE,
        "application/x-tar",
    )
    assert _context(storage, server.model_copy(update={"tar_key": changed.storage_key})) is None
    assert (
        _context(storage, server.model_copy(update={"tar_key": ok.storage_key[:-64] + "0" * 64}))
        is None
    )


# --- WG29-T04 ------------------------------------------------------------------------------------


def test_wg29_t04_no_build_host_is_recorded_not_raised(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    inp, _ = loaded(ms01_files)
    store = MemoryImageStore(inp)
    build_for(store, make_builder("none", 900), ref())
    assert store.saved == [] and [f.code for f in store.failures] == ["BUILDER_DISABLED"]
    assert isinstance(make_builder("docker", 900), DockerCliBuilder)


def test_wg29_t04_a_failed_build_is_recorded_a_missing_daemon_is_retried(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    inp, _ = loaded(ms01_files)
    store = MemoryImageStore(inp)
    build_for(store, FakeBuilder(error=ImageBuildError("BUILD_FAILED", "step 3 failed")), ref())
    assert [f.code for f in store.failures] == ["BUILD_FAILED"] and store.saved == []
    away = FakeBuilder(error=ImageBuildError("DAEMON_UNREACHABLE", "down", retryable=True))
    with pytest.raises(ApplicationError) as err:
        build_for(MemoryImageStore(inp), away, ref())
    assert err.value.type == "DAEMON_UNREACHABLE" and not err.value.non_retryable


def test_wg29_t04_a_stale_context_or_missing_server_is_recorded(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    store = MemoryImageStore(ImageBuildError("SERVER_NOT_STORED", "none"))
    builder = FakeBuilder()
    build_for(store, builder, ref())
    assert [f.code for f in store.failures] == ["SERVER_NOT_STORED"] and builder.calls == []


def test_wg29_t04_a_digest_that_is_not_a_digest_is_recorded(
    ms01_files: dict[str, bytes],  # noqa: F811
) -> None:
    inp, _ = loaded(ms01_files)
    store = MemoryImageStore(inp)
    build_for(store, FakeBuilder(digest="latest"), ref())
    assert [f.code for f in store.failures] == ["ENTRY_INVALID"] and store.saved == []


# --- WG29-T05 ------------------------------------------------------------------------------------


class Run:
    def __init__(self, returncode: int = 0, stderr: bytes = b"", iid: str = DIGEST) -> None:
        self.returncode, self.stderr, self.iid = returncode, stderr, iid
        self.commands: list[list[str]] = []
        self.kwargs: dict[str, Any] = {}
        self.context: dict[str, bytes] = {}

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        self.kwargs = kwargs
        root = Path(command[-1])
        self.context = {
            str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
        if self.returncode == 0:
            Path(command[command.index("--iidfile") + 1]).write_text(self.iid + "\n")
        return subprocess.CompletedProcess(command, self.returncode, b"", self.stderr)


def docker(monkeypatch: pytest.MonkeyPatch, run: Run) -> DockerCliBuilder:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", run)
    return DockerCliBuilder(timeout_seconds=60)


def test_wg29_t05_one_docker_build_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run()
    files = {"Dockerfile": b"FROM scratch\n", "vendor/lib@c/lib.py": b"X = 1\n"}
    built = docker(monkeypatch, run).build(files, "wf_x:abc")
    assert built == BuiltImage(DIGEST)
    [command] = run.commands
    assert command[:2] == ["/usr/bin/docker", "build"] and "wf_x:abc" in command
    assert run.context == files  # the context is the files and nothing else
    assert run.kwargs["timeout"] == 60 and "shell" not in run.kwargs


def test_wg29_t05_the_build_sees_a_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBAM_SECRET_TOKEN", "x")
    monkeypatch.setenv("DOCKER_HOST", "tcp://build-host:2375")
    run = Run()
    docker(monkeypatch, run).build({"Dockerfile": b"FROM scratch\n"}, "wf_x:abc")
    assert "SBAM_SECRET_TOKEN" not in run.kwargs["env"]
    assert run.kwargs["env"]["DOCKER_HOST"] == "tcp://build-host:2375"


@pytest.mark.parametrize(
    ("files", "tag", "code"),
    [
        ({"Dockerfile": b"x"}, "Bad Tag", "TAG_INVALID"),
        ({"a.py": b"x"}, "wf_x:abc", "CONTEXT_INVALID"),
        ({"Dockerfile": b"x", "../out.py": b"x"}, "wf_x:abc", "CONTEXT_INVALID"),
    ],
)
def test_wg29_t05_a_bad_tag_or_context_is_refused_before_docker_runs(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes], tag: str, code: str
) -> None:
    run = Run()
    with pytest.raises(ImageBuildError) as err:
        docker(monkeypatch, run).build(files, tag)
    assert err.value.code == code and run.commands == []


def test_wg29_t05_failures_keep_the_daemons_last_words_and_say_whether_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {"Dockerfile": b"FROM scratch\n"}
    with pytest.raises(ImageBuildError) as failed:
        docker(monkeypatch, Run(1, b"COPY failed: no source files")).build(files, "wf_x:abc")
    assert failed.value.code == "BUILD_FAILED" and not failed.value.retryable
    assert "COPY failed" in str(failed.value)
    down = b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
    with pytest.raises(ImageBuildError) as away:
        docker(monkeypatch, Run(1, down)).build(files, "wf_x:abc")
    assert away.value.code == "DAEMON_UNREACHABLE" and away.value.retryable
    with pytest.raises(ImageBuildError) as odd:
        docker(monkeypatch, Run(0, iid="not-a-digest")).build(files, "wf_x:abc")
    assert odd.value.code == "DIGEST_UNREADABLE"


def test_wg29_t05_the_disabled_builder_never_builds() -> None:
    with pytest.raises(ImageBuildError) as err:
        DisabledBuilder().build({"Dockerfile": b"x"}, "wf_x:abc")
    assert err.value.code == "BUILDER_DISABLED"


# --- WG29-T06: the database store ------------------------------------------------------


def sql_world(tmp_path: Path, files: dict[str, bytes]) -> tuple[World, LocalDiskBackend, str]:
    """A Workflow with a manifest draft and its server files stored (the earlier stages' work)."""
    world = World()
    storage = LocalDiskBackend(tmp_path)
    inp, bundle = loaded(files)
    stored = storage.put(
        bundle_tar(inp.files), StorageScope(WF_TENANT, WF_PROJECT), Kind.CODE, "application/x-tar"
    )
    server = inp.server.model_copy(update={"tar_key": stored.storage_key})
    draft = WorkflowManifest.sealed(workflow())
    with world.sessions() as session:
        table: Any = AuditLog.__table__
        table.create(session.get_bind(), checkfirst=True)
        audit_record(
            session,
            tenant_id=WF_TENANT,
            actor_id="SYSTEM",
            action=SERVER_BUILT,
            entity_type="workflow",
            entity_id=str(WORKFLOW_ID),
            correlation_id="cid-wg29",
            after=server.model_dump(mode="json"),
            idempotency_key=f"workflow_server:{bundle}",
        )
        session.add(
            WorkflowManifestRow(
                tenant_id=WF_TENANT,
                project_id=WF_PROJECT,
                workflow_id=WORKFLOW_ID,
                build_no=1,
                manifest=json.loads(json.dumps(draft.dump())),
                sha256=draft.integrity.content_sha256,
            )
        )
        session.commit()
    return world, storage, bundle


def wf_ref(tenant: uuid.UUID = WF_TENANT) -> WorkflowRef:
    return WorkflowRef(
        tenant_id=tenant,
        project_id=WF_PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg29",
        workflow_id=WORKFLOW_ID,
    )


def test_wg29_t06_the_row_is_written_once_and_is_current_only_for_its_bundle(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    world, storage, bundle = sql_world(tmp_path, ms01_files)
    store, builder = SqlImageStore(world.sessions, storage), FakeBuilder()
    first = build_for(store, builder, wf_ref())
    again = build_for(store, builder, wf_ref())
    assert first == again and len(builder.calls) == 1
    with world.sessions() as session:
        found = current(session, WF_TENANT, WORKFLOW_ID)
        assert found is not None and found.image_digest == DIGEST and found.bundle_sha256 == bundle
        assert found.manifest_sha256 is not None  # the draft sealed with the entry
        assert current(session, uuid.uuid4(), WORKFLOW_ID) is None  # another tenant sees nothing
        newer = WorkflowServerRecord.model_validate(
            {**_server_row(session), "bundle_sha256": "9" * 64}
        )
        audit_record(
            session,
            tenant_id=WF_TENANT,
            actor_id="SYSTEM",
            action=SERVER_BUILT,
            entity_type="workflow",
            entity_id=str(WORKFLOW_ID),
            correlation_id="cid-wg29",
            after=newer.model_dump(mode="json"),
            idempotency_key="workflow_server:" + "9" * 64,
        )
        session.commit()
        assert current(session, WF_TENANT, WORKFLOW_ID) is None  # the image is of older files


def _server_row(session: Any) -> dict[str, Any]:
    from sbam_api.builds.workflow_server import newest

    row = newest(session, WF_TENANT, WORKFLOW_ID)
    assert row is not None
    return row.model_dump(mode="json")


def test_wg29_t06_another_tenants_workflow_is_not_found(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    world, storage, _ = sql_world(tmp_path, ms01_files)
    with pytest.raises(ApplicationError) as err:
        build_for(SqlImageStore(world.sessions, storage), FakeBuilder(), wf_ref(uuid.uuid4()))
    assert err.value.type == "BuildIdsMismatch"


def test_wg29_t06_a_tampered_tar_is_recorded_and_nothing_is_built(
    tmp_path: Path, ms01_files: dict[str, bytes]  # noqa: F811
) -> None:
    world, storage, _ = sql_world(tmp_path, ms01_files)
    with world.sessions() as session:
        row = _server_row(session)
    bad = storage.put(
        bundle_tar({"Dockerfile": b"FROM evil\n"}),
        StorageScope(WF_TENANT, WF_PROJECT),
        Kind.CODE,
        "application/x-tar",
    )
    with world.sessions() as session:
        audit_record(
            session,
            tenant_id=WF_TENANT,
            actor_id="SYSTEM",
            action=SERVER_BUILT,
            entity_type="workflow",
            entity_id=str(WORKFLOW_ID),
            correlation_id="cid-wg29",
            after={**row, "tar_key": bad.storage_key, "bundle_sha256": "8" * 64},
            idempotency_key="workflow_server:" + "8" * 64,
        )
        session.commit()
    builder = FakeBuilder()
    build_for(SqlImageStore(world.sessions, storage), builder, wf_ref())
    assert builder.calls == []
    with world.sessions() as session:
        assert current(session, WF_TENANT, WORKFLOW_ID) is None
