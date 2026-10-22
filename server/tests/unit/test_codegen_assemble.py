"""E11.3 unit tests: assembly of the MS01.1 bodies into a Black-formatted, hashed, stored bundle.

The bodies are the recorded mock answer for MS01.1 (tests/llm-mock/sbam-codegen/e11_2_ms01_1.json),
the loop spec is the MS01.1 sample compiled by E9.2, and storage is the local disk backend in a
temporary directory.
"""

import asyncio
import hashlib
import io
import tarfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from codegen_cases import Counter, ms01_bodies, ms01_loop_spec, ms01_params
from llm_db import TENANT, llm_sessions, scope
from sbam_api.artifacts.models import Artifact, ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import Kind, StorageScope, StorageUnavailable, StoredObject
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.canonical import canonical_json
from sbam_workers.build import assemble as assemble_module
from sbam_workers.build.assemble import (
    INPUT_MISMATCH,
    Assemble,
    AssembleInputs,
    SqlBundleStore,
)
from sbam_workers.build.codegen import (
    LoadedTemplate,
    RenderedSkeleton,
    load_template,
    render_skeleton,
)
from sbam_workers.build.codegen.bodies import STATE_QUERIES_FIELD
from sbam_workers.build.codegen.bundle import (
    BLACK_CONFIG_SHA256,
    BLACK_MODE,
    BLACK_VERSION,
    LOOP_SPEC_FILE,
    SYNTAX_GATE,
    Assembled,
    assemble_bundle,
    bundle_sha256,
    deterministic_tar,
    make_bundle,
    normalize,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

BEGIN = "# >>> LLM_BODY:action"
END = "# <<< LLM_BODY:action"


@pytest.fixture(scope="module")
def template() -> LoadedTemplate:
    return load_template("fastmcp-loopy", "1.0.0")


def run_assembly(template: LoadedTemplate, bodies: dict[str, Any] | None = None) -> Assembled:
    params = ms01_params()
    return assemble_bundle(
        template,
        render_skeleton(template, params),
        ms01_bodies() if bodies is None else bodies,
        params.get("fresh_state", []),
        ms01_loop_spec(),
    )


def untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        out = {}
        for member in tar.getmembers():
            extracted = tar.extractfile(member)
            assert extracted is not None
            out[member.name] = extracted.read()
        return out


# --- pure assembly --------------------------------------------------------------------------------


def test_e11_3_t01_seven_files_with_matching_hashes(template: LoadedTemplate) -> None:
    out = run_assembly(template)
    assert out.findings == () and out.bundle is not None
    expected = {entry.output for entry in template.files} | {LOOP_SPEC_FILE}
    assert len(expected) == 7 and set(out.bundle.hashes) == expected
    stored = untar(out.bundle.tar)
    assert set(stored) == expected
    for name, data in stored.items():  # recomputed from the archive, not from memory
        assert hashlib.sha256(data).hexdigest() == out.bundle.hashes[name]
    assert stored[LOOP_SPEC_FILE] == ms01_loop_spec()
    assert out.bundle.bundle_sha256 == hashlib.sha256(canonical_json(out.bundle.hashes)).hexdigest()


def test_e11_3_t01_every_file_is_black_clean(template: LoadedTemplate) -> None:
    import black

    out = run_assembly(template)
    assert out.bundle is not None
    for name, data in out.bundle.files.items():
        if name.endswith(".py"):
            text = data.decode("utf-8")
            compile(text, name, "exec")
            assert black.format_str(text, mode=BLACK_MODE) == text


def test_e11_3_t02_reassembly_is_byte_identical(template: LoadedTemplate) -> None:
    first, second = run_assembly(template), run_assembly(template)
    assert first.bundle is not None and second.bundle is not None
    assert first.bundle.tar == second.bundle.tar
    assert first.bundle.bundle_sha256 == second.bundle.bundle_sha256


def test_e11_3_t02_tar_header_is_deterministic(template: LoadedTemplate) -> None:
    out = run_assembly(template)
    assert out.bundle is not None
    with tarfile.open(fileobj=io.BytesIO(out.bundle.tar)) as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == sorted(m.name for m in members)
        for m in members:
            assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == (0, 0, 0, "", "")
            assert m.mode == 0o644 and m.isfile() and m.pax_headers == {}
    # Insertion order of the input never changes the bytes.
    files = dict(out.bundle.files)
    assert deterministic_tar(dict(reversed(files.items()))) == out.bundle.tar


def test_e11_3_t03_slot_markers_preserved(template: LoadedTemplate) -> None:
    out = run_assembly(template)
    assert out.bundle is not None
    text = out.bundle.files["action_tool.py"].decode("utf-8")
    lines = [line.strip() for line in text.splitlines()]
    begin, end = lines.index(BEGIN), lines.index(END)
    inserted = "\n".join(lines[begin + 1 : end])
    first_statement = ms01_bodies()["action_body"].strip().splitlines()[0].strip()
    assert begin < end and first_statement in inserted
    for slot in ("helpers", "check"):
        check = out.bundle.files["check_tool.py"].decode("utf-8")
        assert f"# >>> LLM_BODY:{slot}" in check and f"# <<< LLM_BODY:{slot}" in check


def test_e11_3_t04_crlf_and_tabs_normalised(template: LoadedTemplate) -> None:
    clean = run_assembly(template)
    bodies = ms01_bodies()
    messy = {
        k: (v.replace("    ", "\t").replace("\n", "\r\n") if isinstance(v, str) else v)
        for k, v in bodies.items()
    }
    messy[STATE_QUERIES_FIELD] = {
        k: v.replace("\n", "\r\n") for k, v in bodies[STATE_QUERIES_FIELD].items()
    }
    out = run_assembly(template, messy)
    assert out.findings == () and out.bundle is not None
    for name, data in out.bundle.files.items():
        assert b"\r" not in data and (name == LOOP_SPEC_FILE or b"\t" not in data)
    assert clean.bundle is not None
    assert out.bundle.bundle_sha256 == clean.bundle.bundle_sha256  # same code once normalised


def test_e11_3_normalize() -> None:
    assert normalize("a\r\n\tb\rc") == "a\n    b\nc"
    assert normalize("x = 1\n") == "x = 1\n"


def test_e11_3_t05_unmatched_paren_is_a_syntax_finding(template: LoadedTemplate) -> None:
    out = run_assembly(template, ms01_bodies(action_body="result = verify(otp\nreturn result\n"))
    assert out.bundle is None
    [finding] = out.findings
    assert finding.file == "action_tool.py" and finding.line is not None
    data = finding.as_dict()
    assert data["gate"] == data["code"] == SYNTAX_GATE


def test_e11_3_file_names_come_only_from_the_template() -> None:
    for bad in ("../x.py", "a/b.py", "/etc/passwd", "X.py", "x.sh", ""):
        with pytest.raises(ValueError):
            make_bundle({bad: b""})
    bundle = make_bundle({"b.py": b"", "a.json": b"{}"})
    assert list(bundle.hashes) == ["a.json", "b.py"]
    assert bundle.bundle_sha256 == bundle_sha256(
        {"b.py": bundle.hashes["b.py"], "a.json": bundle.hashes["a.json"]}
    )


def test_e11_3_black_provenance_is_pinned() -> None:
    assert BLACK_VERSION == "26.10.0"  # matches the workers pin; a bump must update both
    assert len(BLACK_CONFIG_SHA256) == 64


# --- the activity ---------------------------------------------------------------------------------


class FullDisk:
    """A storage adapter with no space left: LocalDiskBackend maps ENOSPC to StorageUnavailable."""

    calls = 0

    def put(self, *args: Any, **kwargs: Any) -> StoredObject:
        self.calls += 1
        raise StorageUnavailable()


def stage_input(**over: Any) -> BuildStageInput:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": scope().project_id,
        "run_id": uuid.uuid4(),
        "correlation_id": "corr-1",
        "workflow_id": uuid.uuid4(),
        "sub_skill_id": uuid.uuid4(),
        "build_no": 1,
        "cycle": 0,
        "attempt": 0,
    }
    return BuildStageInput(**{**values, **over})


def db() -> sessionmaker[Session]:
    sessions = llm_sessions()
    tables = [CodeAttempt.__table__, Artifact.__table__]
    CodeAttempt.metadata.create_all(sessions.kw["bind"], tables=tables)  # type: ignore[arg-type]
    return sessions


def ctx(inp: BuildStageInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def seed(
    sessions: sessionmaker[Session],
    template: LoadedTemplate,
    inp: BuildStageInput,
    bodies: dict[str, Any] | None = None,
    status: CodeAttemptStatus = CodeAttemptStatus.BODIES_GENERATED,
) -> uuid.UUID:
    """The row generate_bodies (E11.2) leaves behind."""
    with sessions() as session:
        row = CodeAttemptRepository(session, ctx(inp)).record(
            {
                "workflow_id": inp.workflow_id,
                "sub_skill_id": inp.sub_skill_id,
                "manifest_id": None,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt,
                "template_name": template.name,
                "template_version": template.version,
                "template_sha256": template.sha256,
                "status": status,
                "bodies": ms01_bodies() if bodies is None else bodies,
                "llm_call_ids": [],
                "trace_id": "trace-1",
                "findings": [],
                "created_by": "sbam-worker",
            }
        )
        session.commit()
        return row.id


def activity(
    template: LoadedTemplate, sessions: sessionmaker[Session], storage: Any, **given: Any
) -> Assemble:
    params = ms01_params()
    skeleton: RenderedSkeleton = render_skeleton(template, params)

    async def inputs(inp: BuildStageInput) -> AssembleInputs:
        values: dict[str, Any] = {
            "template": template,
            "params": params,
            "skeleton": skeleton,
            "loop_spec": ms01_loop_spec(),
        }
        return AssembleInputs(**{**values, **given})

    return Assemble(inputs, storage, SqlBundleStore(sessions))


def attempt(sessions: sessionmaker[Session], inp: BuildStageInput) -> CodeAttempt:
    with sessions() as session:
        [row] = CodeAttemptRepository(session, ctx(inp)).list()
        session.expunge_all()
        return row


def artifacts(sessions: sessionmaker[Session], inp: BuildStageInput) -> list[Artifact]:
    with sessions() as session:
        found = ArtifactRepository(session, ctx(inp)).list()
        session.expunge_all()
        return found


def run(impl: Assemble, inp: BuildStageInput) -> uuid.UUID:
    return asyncio.run(ActivityEnvironment().run(impl.run, inp)).id


def test_e11_3_t01_activity_stores_bundle_and_hashes(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = seed(sessions, template, inp)
    assert run(activity(template, sessions, storage), inp) == attempt_id
    row = attempt(sessions, inp)
    assert row.status == CodeAttemptStatus.ASSEMBLED
    assert row.files is not None and len(row.files) == 7
    assert row.bundle_key is not None
    assert row.bundle_key.startswith(f"{inp.tenant_id}/{inp.project_id}/{inp.workflow_id}/code/")
    stored = untar(storage.get(row.bundle_key, verify=True))  # per-file hashes from storage
    assert {name: hashlib.sha256(data).hexdigest() for name, data in stored.items()} == row.files
    assert row.bundle_sha256 == bundle_sha256(row.files)
    assert (row.black_version, row.black_config_sha256) == (BLACK_VERSION, BLACK_CONFIG_SHA256)
    [index] = artifacts(sessions, inp)
    assert index.kind == ArtifactKind.CODE_BUNDLE and index.storage_key == row.bundle_key
    assert index.size == len(storage.get(row.bundle_key))
    assert index.sha256 == row.bundle_key.rsplit("/", 1)[1]


def test_e11_3_t05_activity_routes_syntax_error_to_correction(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    bad = ms01_bodies(action_body="result = verify(otp\nreturn result\n")
    attempt_id = seed(sessions, template, inp, bad)
    impl = activity(template, sessions, storage)
    with pytest.raises(ApplicationError) as caught:
        run(impl, inp)
    assert caught.value.type == GATE_FAILED and caught.value.non_retryable
    assert caught.value.details[0] == [str(attempt_id)]
    row = attempt(sessions, inp)
    assert row.status == CodeAttemptStatus.ASSEMBLY_FAILED and row.bundle_key is None
    assert [f["gate"] for f in row.findings] == [SYNTAX_GATE]
    assert artifacts(sessions, inp) == []
    with pytest.raises(ApplicationError) as again:  # a retried stage gives the same answer
        run(impl, inp)
    assert again.value.type == GATE_FAILED


class CrashAfterUpload(SqlBundleStore):
    """The worker dies after the tar is stored and before the row is updated."""

    crashed = False

    def assembled(self, *args: Any, **kwargs: Any) -> None:
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("worker lost")
        super().assembled(*args, **kwargs)


def test_e11_3_t06_retry_after_upload_is_idempotent(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    attempt_id = seed(sessions, template, inp)
    impl = activity(template, sessions, storage)
    impl.attempts = CrashAfterUpload(sessions)
    with pytest.raises(RuntimeError, match="worker lost"):
        run(impl, inp)
    assert attempt(sessions, inp).status == CodeAttemptStatus.BODIES_GENERATED
    assert run(impl, inp) == attempt_id
    assert run(impl, inp) == attempt_id  # a third run finds ASSEMBLED and does nothing
    [index] = artifacts(sessions, inp)
    [row] = [attempt(sessions, inp)]
    assert index.storage_key == row.bundle_key
    code_dir = tmp_path / str(inp.tenant_id) / str(inp.project_id) / str(inp.workflow_id) / "code"
    assert len(list(code_dir.iterdir())) == 1  # content-addressed: one stored object


def test_e11_3_t06_identical_bundle_dedupes_to_one_artifact(
    template: LoadedTemplate, tmp_path: Path
) -> None:
    """A correction that reproduces the same code stores nothing new and reuses the index row."""
    sessions, storage = db(), LocalDiskBackend(tmp_path)
    first = stage_input()
    second = stage_input(workflow_id=first.workflow_id, sub_skill_id=first.sub_skill_id, attempt=1)
    seed(sessions, template, first)
    seed(sessions, template, second)
    impl = activity(template, sessions, storage)
    run(impl, first)
    run(impl, second)
    with sessions() as session:
        rows = CodeAttemptRepository(session, ctx(first)).list()
        assert len({row.bundle_key for row in rows}) == 1
    assert len(artifacts(sessions, first)) == 1


def test_e11_3_t07_storage_full_is_retryable_and_leaves_no_trace(
    template: LoadedTemplate, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = Counter()
    monkeypatch.setattr(assemble_module, "_storage_failures", counter)
    sessions, storage, inp = db(), FullDisk(), stage_input()
    seed(sessions, template, inp)
    with pytest.raises(StorageUnavailable):  # not an ApplicationError: Temporal retries it
        run(activity(template, sessions, storage), inp)
    row = attempt(sessions, inp)
    assert row.status == CodeAttemptStatus.BODIES_GENERATED and row.bundle_key is None
    assert artifacts(sessions, inp) == []
    assert counter.value == 1 and storage.calls == 1


def test_e11_3_skeleton_must_match_the_attempt(template: LoadedTemplate, tmp_path: Path) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    seed(sessions, template, inp)
    impl = activity(template, sessions, storage, manifest_id=uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        run(impl, inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable
    assert attempt(sessions, inp).status == CodeAttemptStatus.BODIES_GENERATED


@pytest.mark.parametrize("status", [None, CodeAttemptStatus.PRECHECK_FAILED])
def test_e11_3_needs_generated_bodies(
    template: LoadedTemplate, tmp_path: Path, status: CodeAttemptStatus | None
) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    if status is not None:
        seed(sessions, template, inp, status=status)
    with pytest.raises(ApplicationError) as caught:
        run(activity(template, sessions, storage), inp)
    assert caught.value.type == INPUT_MISMATCH and caught.value.non_retryable


def test_e11_3_bundles_are_tenant_scoped(template: LoadedTemplate, tmp_path: Path) -> None:
    sessions, storage, inp = db(), LocalDiskBackend(tmp_path), stage_input()
    seed(sessions, template, inp)
    run(activity(template, sessions, storage), inp)
    with sessions() as session:
        other = ArtifactRepository(session, TenantContext(uuid.uuid4(), inp.project_id))
        assert other.list() == []
    scope_ = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
    row = attempt(sessions, inp)
    assert row.bundle_key is not None
    assert row.bundle_key == scope_.key(Kind.CODE, row.bundle_key.rsplit("/", 1)[1])
