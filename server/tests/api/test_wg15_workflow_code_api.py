"""WG-15 (#763): the whole Workflow's code, read only, through the real app and its storage."""

import hashlib
import io
import tarfile
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import test_code_view_api as code_tests
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.manifests.models import GateResultRow, SubSkillManifest, WorkflowManifestRow
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.code_view import MAX_VIEW_BYTES
from sbam_contracts.repo_overlay import PatchRecord, RepoOverlay
from sqlalchemy import select
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_code_view_api import Harness as CodeHarness
from test_code_view_api import source

COMMIT = "a" * 40
REPO = f"lib@{COMMIT}"
FILES = {
    "action_tool.py": source("        a = 1"),
    "check_tool.py": source("        b = 2"),
    f"vendor/{REPO}/LICENSE": b"MIT License\n",
    f"vendor/{REPO}/lib.py": b"X = 1\n",
    f"patches/{REPO}/0001-fix.patch": b"--- a/lib.py\n+++ b/lib.py\n",
    "overlay/lib/lib.py": b"X = 2\n",
    "overlay/lib/other.py": b"Y = 1\n",
}
TAGS = {
    "action_tool.py": "generated",
    "check_tool.py": "generated",
    f"vendor/{REPO}/LICENSE": "reused",
    f"vendor/{REPO}/lib.py": "reused",
    f"patches/{REPO}/0001-fix.patch": "customized",
    "overlay/lib/lib.py": "customized",
    "overlay/lib/other.py": "reused",
}


def nested_bundle(files: Mapping[str, bytes]) -> Any:
    """A tar like `make_bundle`'s, but with `vendor/...`, `patches/...` and `overlay/...` paths."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            tar.addfile(info, io.BytesIO(files[name]))
    hashes = {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    return SimpleNamespace(tar=out.getvalue(), hashes=hashes)


class Harness(CodeHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for model in (SubSkillManifest, GateResultRow, WorkflowManifestRow, WorkflowSchema):
            model.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]

    def overlay_manifest(self, sub: uuid.UUID | None = None) -> None:
        """A Skill manifest that records `lib`, one patch touching lib.py."""
        repo = RepoOverlay(
            url="https://github.com/acme/lib",
            commit=COMMIT,
            licence="MIT",
            repo_dir=f"vendor/{REPO}",
            licence_files=["LICENSE"],
            vendor_tree_sha256="0" * 64,
            patches=[
                PatchRecord(
                    path=f"patches/{REPO}/0001-fix.patch",
                    sha256="1" * 64,
                    reason="a stated reason",
                    files=["lib.py"],
                )
            ],
            patched_tree_sha256="2" * 64,
        )
        with self.sessions() as s:
            s.add(
                SubSkillManifest(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    sub_skill_id=sub or self.sub,
                    build_no=1,
                    cycle=0,
                    attempt=0,
                    manifest={"build": {"repos": [repo.model_dump(mode="json")]}},
                    sha256="sha256:" + "0" * 64,
                    runtime_digest="sha256:" + "1" * 64,
                )
            )
            s.commit()

    def gate(self, gate: str, status: str, attempt_no: int = 0) -> None:
        with self.sessions() as s:
            attempt = s.scalars(select(CodeAttempt).where(CodeAttempt.attempt == attempt_no)).one()
            s.add(
                GateResultRow(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    sub_skill_id=self.sub,
                    code_attempt_id=attempt.id,
                    gate=gate,
                    status=status,
                    findings=[],
                )
            )
            s.commit()

    def workflow_manifest(self, manifest: dict[str, Any]) -> None:
        with self.sessions() as s:
            s.add(
                WorkflowManifestRow(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    workflow_id=self.workflow_id,
                    build_no=1,
                    manifest=manifest,
                    sha256="sha256:" + "3" * 64,
                )
            )
            s.commit()

    def schema(self, sql: bytes, source_kind: str = "GENERATED") -> None:
        scope = StorageScope(TENANT_A, uuid.UUID(self.project), self.workflow_id)
        stored = self.storage.put(sql, scope, Kind.DERIVED, "text/plain")
        with self.sessions() as s:
            s.add(
                WorkflowSchema(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    workflow_id=self.workflow_id,
                    version=1,
                    source=source_kind,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    tables=["t"],
                    status="PASSED",
                    findings=[],
                )
            )
            s.commit()

    def wf(self, tail: str = "", token: str | None = None, wf: uuid.UUID | None = None) -> Any:
        url = f"{self.path(wf or self.workflow_id)}/code{tail}"
        return self.call("GET", url, token or self.fde())


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    # The generator's bundles are flat today; the overlay layout (WG-9) nests `vendor/...`.
    monkeypatch.setattr(code_tests, "make_bundle", nested_bundle)
    yield Harness(tmp_path)


def groups(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {g["label"]: g for g in body["groups"]}


def test_wg15_t01_tags_match_provenance(h: Harness) -> None:
    h.attempt(0, FILES)
    h.overlay_manifest()
    body = h.wf().json()
    sub = next(g for g in body["groups"] if g["kind"] == "sub_skill")
    assert {f["path"]: f["tag"] for f in sub["files"]} == TAGS
    assert [f["path"] for f in sub["files"]] == sorted(FILES)  # every file once, in path order


def test_wg15_without_a_recorded_repository_only_the_layout_tags_apply(h: Harness) -> None:
    h.attempt(0, FILES)  # no Skill manifest with `build.repos`
    sub = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")
    tags = {f["path"]: f["tag"] for f in sub["files"]}
    assert tags["overlay/lib/lib.py"] == "reused"  # nothing says a patch touched it
    assert tags[f"patches/{REPO}/0001-fix.patch"] == "customized"
    assert tags["action_tool.py"] == "generated"


def test_wg15_the_newest_attempt_is_shown_with_its_gates(h: Harness) -> None:
    h.attempt(0, {"action_tool.py": source("        a = 1")}, status="ASSEMBLY_FAILED")
    h.attempt(1, FILES)
    h.gate("AST_SYNTAX", "PASSED", attempt_no=1)
    sub = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")
    assert (sub["attempt"], sub["status"]) == (1, "ASSEMBLED")
    assert sub["gates"] == [{"gate": "AST_SYNTAX", "status": "PASSED"}]
    assert len(sub["files"]) == len(FILES)


def test_wg15_a_sub_skill_without_a_build_is_listed_with_no_files(h: Harness) -> None:
    sub = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")
    assert sub["files"] == [] and sub["status"] is None


def test_wg15_the_workflow_group_lists_its_manifest_and_schema(h: Harness) -> None:
    h.workflow_manifest({"name": "wf", "version": "1.0.0"})
    h.schema(b"CREATE TABLE t (id int);\n")
    body = h.wf().json()
    flow = groups(body)["Workflow"]
    assert flow["kind"] == "workflow" and flow["group_id"] == "workflow"
    assert {f["path"]: f["tag"] for f in flow["files"]} == {
        "manifest.json": "generated",
        "schema.sql": "generated",
    }
    view = h.wf("/files?group=workflow&path=schema.sql").json()
    assert view["content"] == "CREATE TABLE t (id int);\n" and view["truncated"] is False
    manifest = h.wf("/files?group=workflow&path=manifest.json").json()
    assert '"name": "wf"' in manifest["content"]


def test_wg15_an_uploaded_schema_is_not_tagged_generated(h: Harness) -> None:
    h.schema(b"CREATE TABLE u (id int);\n", source_kind="UPLOADED")
    flow = groups(h.wf().json())["Workflow"]
    assert {f["path"]: f["tag"] for f in flow["files"]} == {"schema.sql": "reused"}


def test_wg15_a_file_view_has_the_bytes_the_tag_and_the_hash(h: Harness) -> None:
    h.attempt(0, FILES)
    h.overlay_manifest()
    sub = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")
    view = h.wf(f"/files?group={sub['group_id']}&path=overlay/lib/lib.py").json()
    assert (view["content"], view["tag"]) == ("X = 2\n", "customized")
    assert view["sha256"] == next(f["sha256"] for f in sub["files"] if f["path"] == view["path"])


def test_wg15_a_file_over_the_cap_is_cut_and_flagged(h: Harness) -> None:
    big = b"x" * (MAX_VIEW_BYTES + 10)
    h.attempt(0, {"data.txt": big})
    group = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")["group_id"]
    view = h.wf(f"/files?group={group}&path=data.txt").json()
    assert view["truncated"] is True and len(view["content"]) == MAX_VIEW_BYTES


def test_wg15_t03_findings_link_to_their_file_and_line(h: Harness) -> None:
    finding = {
        "gate": "AST_SYNTAX",
        "code": "AST_FORBIDDEN_CALL",
        "file": "action_tool.py",
        "line": 3,
        "col": 8,
        "message": "eval is forbidden",
    }
    general = {"gate": "LIBRARIES", "code": "LIB_UNPINNED", "message": "unpinned"}
    h.attempt(0, FILES, findings=[finding, general])
    sub = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")
    on_file = next(f for f in sub["files"] if f["path"] == "action_tool.py")
    assert [(x["code"], x["line"], x["col"]) for x in on_file["findings"]] == [
        ("AST_FORBIDDEN_CALL", 3, 8)
    ]
    assert all(f["findings"] == [] for f in sub["files"] if f["path"] != "action_tool.py")
    assert [x["code"] for x in sub["findings"]] == ["LIB_UNPINNED"]  # no file: on the group
    view = h.wf(f"/files?group={sub['group_id']}&path=action_tool.py").json()
    assert [x["line"] for x in view["findings"]] == [3]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_wg15_t02_no_write_route_exists(h: Harness, method: str) -> None:
    h.attempt(0, FILES)
    for url in (
        f"{h.path(h.workflow_id)}/code",
        f"{h.path(h.workflow_id)}/code/files?group=workflow&path=manifest.json",
    ):
        assert h.call(method, url, h.fde()).status_code == 405


def test_wg15_t04_other_tenants_unassigned_fdes_and_unknown_ids_get_404(h: Harness) -> None:
    h.attempt(0, FILES)
    h.workflow_manifest({"name": "wf"})
    group = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")["group_id"]
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other, unassigned):
        problem(h.wf(token=token), 404, "NOT_FOUND")
        problem(h.wf(f"/files?group={group}&path=action_tool.py", token), 404, "NOT_FOUND")
        problem(h.wf("/files?group=workflow&path=manifest.json", token), 404, "NOT_FOUND")
    problem(h.wf(wf=uuid7()), 404, "NOT_FOUND")  # an invented Workflow id


@pytest.mark.parametrize(
    "tail",
    [
        "/files?group=not-a-uuid&path=action_tool.py",
        f"/files?group={uuid7()}&path=action_tool.py",  # a Sub-Skill that does not exist
        "/files?group=workflow&path=action_tool.py",  # a Sub-Skill file asked of the Workflow group
        "/files?group=workflow&path=../manifest.json",
        "/files?group=workflow&path=nope.sql",
    ],
)
def test_wg15_a_group_or_path_the_stored_maps_do_not_hold_is_404(h: Harness, tail: str) -> None:
    h.attempt(0, FILES)
    h.workflow_manifest({"name": "wf"})
    problem(h.wf(tail), 404, "NOT_FOUND")


def test_wg15_a_path_outside_the_sub_skills_file_map_is_404(h: Harness) -> None:
    h.attempt(0, FILES)
    group = next(g for g in h.wf().json()["groups"] if g["kind"] == "sub_skill")["group_id"]
    for path in ("missing.py", "../action_tool.py", "/etc/passwd", "action_tool.py/"):
        problem(h.wf(f"/files?group={group}&path={path}"), 404, "NOT_FOUND")


def test_wg15_a_sub_skill_of_another_workflow_is_404_on_this_one(h: Harness) -> None:
    other_workflow = h.workflow(2, sow_version=h.generated(h.structured))
    foreign = h.sub_skill(other_workflow)
    problem(h.wf(f"/files?group={foreign}&path=action_tool.py"), 404, "NOT_FOUND")
