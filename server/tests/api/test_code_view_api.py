"""E11.5 code viewer API: attempts, one file of a bundle, and a diff, over real stored bundles."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import SubSkill
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.code_view import MAX_VIEW_BYTES
from sbam_workers.build.codegen.bundle import make_bundle
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_workflows_api import Harness as WorkflowHarness

SKELETON = (
    "def action(ctx):\n"
    "    # >>> LLM_BODY:action_body\n"
    "{body}\n"
    "    # <<< LLM_BODY:action_body\n"
    "    return ok\n"
)


def source(body: str) -> bytes:
    return SKELETON.format(body=body).encode()


class Harness(WorkflowHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        CodeAttempt.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        SubSkill.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.workflow_id = self.workflow()
        self.sub = self.sub_skill(self.workflow_id)

    def sub_skill(self, workflow_id: uuid.UUID, *, tenant: uuid.UUID = TENANT_A) -> uuid.UUID:
        """A Sub-Skill row of that workflow (the attempts view 404s an id it does not know)."""
        with self.sessions() as s:
            row = SubSkill(
                id=uuid7(),
                tenant_id=tenant,
                project_id=uuid.UUID(self.project),
                macro_skill_id=uuid7(),
                workflow_id=workflow_id,
                skill_id=f"MS01.{uuid.uuid4().hex[:6]}_Check",
                skill_name="Check",
                order=1,
                isatvon={},
                isatvon_framework_version="1.4",
                sow_refs=[],
                validation_errors=[],
                content_sha256="0" * 64,
            )
            row.state = "VALID"
            s.add(row)
            s.commit()
            return row.id

    def attempt(
        self,
        n: int,
        files: dict[str, bytes] | None,
        *,
        build_no: int = 1,
        cycle: int = 0,
        findings: list[dict[str, Any]] | None = None,
        status: str = "ASSEMBLED",
        sub: uuid.UUID | None = None,
    ) -> None:
        key: str | None = None
        hashes: dict[str, str] | None = None
        if files is not None:
            bundle = make_bundle(files)
            scope = StorageScope(TENANT_A, uuid.UUID(self.project), self.workflow_id)
            key = self.storage.put(bundle.tar, scope, Kind.CODE, "application/x-tar").storage_key
            hashes = dict(bundle.hashes)
        with self.sessions() as s:
            s.add(
                CodeAttempt(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    workflow_id=self.workflow_id,
                    sub_skill_id=sub or self.sub,
                    build_no=build_no,
                    cycle=cycle,
                    attempt=n,
                    template_name="t",
                    template_version="1",
                    template_sha256="0" * 64,
                    status=status,
                    bodies={},
                    trace_id="tr",
                    findings=findings or [],
                    bundle_key=key,
                    files=hashes,
                    correction_reason="fix it" if n else None,
                )
            )
            s.commit()

    def code(self, tail: str, token: str | None = None, sub: uuid.UUID | None = None) -> Any:
        base = f"{self.path(self.workflow_id)}/sub-skills/{sub or self.sub}/code"
        return self.call("GET", base + tail, token or self.fde())


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e11_5_t01_diff_shows_only_the_corrected_lines(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1\n        b = 2")})
    h.attempt(1, {"action.py": source("        a = 1\n        b = 3")})
    body = h.code("/attempts/0/diff/1?path=action.py").json()
    assert body["changed"] is True and body["truncated"] is False
    changes = [
        x for x in body["unified"].splitlines() if x[:1] in "+-" and x[:3] not in ("+++", "---")
    ]
    assert changes == ["-        b = 2", "+        b = 3"]


def test_e11_5_t02_slots_are_the_lines_between_the_body_markers(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1\n        b = 2")})
    view = h.code("/attempts/0/files?path=action.py").json()
    assert view["slots"] == [{"start": 3, "end": 4}]
    assert view["content"].splitlines()[2] == "        a = 1"


def test_e11_5_t03_findings_arrive_with_the_file_and_line(h: Harness) -> None:
    finding = {
        "gate": "AST",
        "code": "AST_FORBIDDEN_CALL",
        "file": "action.py",
        "line": 3,
        "col": 8,
        "message": "eval is forbidden",
    }
    other = {"gate": "AST", "code": "AST_SYNTAX", "file": "check.py", "line": 1, "message": "x"}
    h.attempt(0, {"action.py": source("        eval(x)")}, findings=[finding, other])
    view = h.code("/attempts/0/files?path=action.py").json()
    assert [(f["code"], f["line"], f["col"]) for f in view["findings"]] == [
        ("AST_FORBIDDEN_CALL", 3, 8)
    ]
    listed = h.code("/attempts").json()["items"][0]
    assert len(listed["findings"]) == 2


@pytest.mark.parametrize(
    "path", ["../action.py", "/etc/passwd", "..%2F..%2Fetc%2Fpasswd", "missing.py", "action.py/"]
)
def test_e11_5_t04_a_path_outside_the_file_map_is_404(h: Harness, path: str) -> None:
    h.attempt(0, {"action.py": source("        a = 1")})
    problem(h.code(f"/attempts/0/files?path={path}"), 404, "NOT_FOUND")
    problem(h.code(f"/attempts/0/diff/0?path={path}"), 404, "NOT_FOUND")


def test_e11_5_t05_other_tenants_unassigned_fdes_and_other_projects_get_404(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1")})
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other, unassigned):
        for tail in ("/attempts", "/attempts/0/files?path=action.py"):
            problem(h.code(tail, token), 404, "NOT_FOUND")
    problem(h.code("/attempts", sub=uuid7()), 404, "NOT_FOUND")  # an invented Sub-Skill id (A-33)


def test_a33_t01_a_sub_skill_without_attempts_is_an_empty_list_not_a_404(h: Harness) -> None:
    assert h.code("/attempts").json() == {"items": []}  # it exists, nothing generated yet


def test_a33_t02_a_sub_skill_of_another_workflow_is_404_on_this_one(h: Harness) -> None:
    other_workflow = h.workflow(2, sow_version=h.generated(h.structured))
    foreign = h.sub_skill(other_workflow)
    h.attempt(0, {"action.py": source("        a = 1")}, sub=foreign)
    problem(h.code("/attempts", sub=foreign), 404, "NOT_FOUND")  # not this workflow's Sub-Skill
    assert len(h.code("/attempts").json()["items"]) == 0


def test_a33_t03_a_sub_skill_of_another_tenant_is_404(h: Harness) -> None:
    foreign = h.sub_skill(h.workflow_id, tenant=TENANT_B)
    problem(h.code("/attempts", sub=foreign), 404, "NOT_FOUND")


def test_e11_5_t05b_a_workflow_of_another_project_is_404(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1")})
    stranger = h.workflow(2, sow_version=h.generated(h.structured))
    from sbam_api.workflows.models import Workflow

    with h.sessions() as s:
        row = s.get(Workflow, stranger)
        assert row is not None
        row.project_id = uuid.uuid4()
        s.commit()
    url = f"{h.path(stranger)}/sub-skills/{h.sub}/code/attempts"
    problem(h.call("GET", url, h.fde()), 404, "NOT_FOUND")


def test_e11_5_t06_no_attempts_is_an_empty_list_and_a_missing_attempt_404(h: Harness) -> None:
    assert h.code("/attempts").json() == {"items": []}
    problem(h.code("/attempts/0/files?path=action.py"), 404, "NOT_FOUND")


def test_e11_5_a_failed_assembly_lists_no_files(h: Harness) -> None:
    h.attempt(
        0,
        None,
        status="ASSEMBLY_FAILED",
        findings=[
            {"gate": "AST", "code": "AST_SYNTAX", "file": "action.py", "line": 2, "message": "bad"}
        ],
    )
    item = h.code("/attempts").json()["items"][0]
    assert item["files"] == [] and item["status"] == "ASSEMBLY_FAILED"
    problem(h.code("/attempts/0/files?path=action.py"), 404, "NOT_FOUND")


def test_e11_5_b_identical_attempts_diff_is_unchanged(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1")})
    h.attempt(1, {"action.py": source("        a = 1")})
    body = h.code("/attempts/0/diff/1?path=action.py").json()
    assert body["changed"] is False and body["unified"] == ""


def test_e11_5_c_the_newest_build_and_cycle_are_the_default(h: Harness) -> None:
    h.attempt(0, {"action.py": source("        a = 1")}, build_no=1)
    h.attempt(0, {"action.py": source("        a = 2")}, build_no=2, cycle=0)
    h.attempt(0, {"action.py": source("        a = 3")}, build_no=2, cycle=1)
    newest = h.code("/attempts/0/files?path=action.py").json()["content"]
    assert "a = 3" in newest
    older = h.code("/attempts/0/files?path=action.py&build_no=1&cycle=0").json()["content"]
    assert "a = 1" in older


def test_e11_5_d_a_file_over_the_cap_is_cut_and_flagged(h: Harness) -> None:
    h.attempt(0, {"action.py": b"x = 1\n" * (MAX_VIEW_BYTES // 4)})
    view = h.code("/attempts/0/files?path=action.py").json()
    assert view["truncated"] is True and len(view["content"].encode()) <= MAX_VIEW_BYTES


def test_e11_5_e_an_attempt_number_past_two_is_422(h: Harness) -> None:
    assert h.code("/attempts/3/files?path=action.py").status_code == 422
