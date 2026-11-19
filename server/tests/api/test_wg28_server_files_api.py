"""WG-28 (#862): the code preview lists the generated server files the build stored, tagged
`generated`, and reads them from storage, checked against the recorded hash. Read only."""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_code_view_api as code_tests
import test_wg15_workflow_code_api as wg15
from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import record
from sbam_api.builds.workflow_server import BUILT_ACTION, ENTITY, WorkflowServerRecord
from sbam_api.storage.base import Kind, StorageScope
from sbam_workers.repo_fetch.report import bundle_tar
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_wg15_workflow_code_api import Harness

SERVER = {
    "server.py": b"mcp = FastMCP('wf_x')\n",
    "tools.json": b"[]\n",
    "Dockerfile": b"FROM x\n",
    "loop_specs/MS01_1.json": b"{}\n",
}


class ServerHarness(Harness):
    def store_server(self, files: dict[str, bytes] = SERVER, tamper: bool = False) -> None:
        scope = StorageScope(TENANT_A, uuid.UUID(self.project), self.workflow_id)
        stored = self.storage.put(
            bundle_tar({**files, "server.py": b"changed\n"} if tamper else files),
            scope,
            Kind.CODE,
            "application/x-tar",
        )
        rec = WorkflowServerRecord(
            server_name="wf_x",
            bundle_sha256="a" * 64,
            tar_key=stored.storage_key,
            files={p: hashlib.sha256(d).hexdigest() for p, d in files.items()},
            tools=["MS01_1__action", "MS01_1__check"],
            base_layer_key="b" * 64,
            top_layer_key="c" * 64,
        )
        with self.sessions() as s:
            AuditLog.__table__.create(s.get_bind(), checkfirst=True)  # type: ignore[attr-defined]
            record(
                s,
                tenant_id=TENANT_A,
                actor_id="SYSTEM",
                action=BUILT_ACTION,
                entity_type=ENTITY,
                entity_id=str(self.workflow_id),
                correlation_id="cid-wg28",
                after=rec.model_dump(mode="json"),
                idempotency_key="workflow_server:" + "a" * 64,
            )
            s.commit()


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServerHarness]:
    monkeypatch.setattr(code_tests, "make_bundle", wg15.nested_bundle)
    yield ServerHarness(tmp_path)


def flow(h: ServerHarness) -> dict[str, Any]:
    return wg15.groups(h.wf().json())["Workflow"]


def test_wg28_t07_a_workflow_without_stored_server_files_lists_none(h: ServerHarness) -> None:
    h.schema(b"CREATE TABLE t (id int);\n")
    assert [f["path"] for f in flow(h)["files"]] == ["schema.sql"]  # an empty state, not an error


def test_wg28_t07_the_stored_server_files_are_listed_with_the_generated_tag(
    h: ServerHarness,
) -> None:
    h.schema(b"CREATE TABLE t (id int);\n")
    h.store_server({**SERVER, "schema.sql": b"CREATE TABLE t (id int);\n"})
    files = flow(h)["files"]
    assert {f["path"]: f["tag"] for f in files} == {
        "schema.sql": "generated",  # listed once, from its own record
        "Dockerfile": "generated",
        "loop_specs/MS01_1.json": "generated",
        "server.py": "generated",
        "tools.json": "generated",
    }
    view = h.wf("/files?group=workflow&path=server.py").json()
    assert view["content"] == "mcp = FastMCP('wf_x')\n" and view["tag"] == "generated"
    assert view["sha256"] == hashlib.sha256(SERVER["server.py"]).hexdigest()


def test_wg28_t07_a_path_that_is_not_listed_or_a_tampered_tar_is_404(h: ServerHarness) -> None:
    h.store_server(tamper=True)
    assert h.wf("/files?group=workflow&path=server.py").status_code == 404  # hash mismatch
    assert h.wf("/files?group=workflow&path=../etc/passwd").status_code == 404
    assert h.wf("/files?group=workflow&path=other.py").status_code == 404


def test_wg28_t08_other_tenants_unassigned_fdes_and_unknown_ids_get_404(h: ServerHarness) -> None:
    h.store_server()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other, unassigned):
        assert h.wf("", token).status_code == 404
        assert h.wf("/files?group=workflow&path=server.py", token).status_code == 404
    assert h.wf("", wf=uuid.uuid4()).status_code == 404
    problem(h.wf("", other), 404, "NOT_FOUND")
