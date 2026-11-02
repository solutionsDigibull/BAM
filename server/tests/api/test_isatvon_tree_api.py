"""E8.7 `GET W/macro-skills` through the real app and auth chain: the tree the ISATVON workbench
loads first. Without it the workbench answered 404 "Not found" for every Workflow."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.sow.models import SowVersion
from sbam_api.workflows.models import Workflow
from sbam_contracts.isatvon_workbench import WorkbenchTree
from sqlalchemy import update
from test_auth import ADMIN_A, FDE_A2, TENANT_B, problem
from test_isatvon_export_api import IDEF, Harness, sample


class TreeHarness(Harness):
    def tree(self, token: str | None = None, workflow: uuid.UUID | None = None) -> Any:
        path = f"/projects/{self.project}/workflows/{workflow or self.wf}/macro-skills"
        return self.call("GET", path, token or self.fde())

    def sow_state(self, state: str) -> None:
        with self.sessions() as s:
            s.execute(update(SowVersion).where(SowVersion.id == self.v1).values(state=state))
            s.commit()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[TreeHarness]:
    yield TreeHarness(tmp_path)


def test_e8_7_the_tree_lists_macros_and_sub_skills_in_order(h: TreeHarness) -> None:
    second = h.macro(2, "MS02_Second")
    first = h.macro(1, "MS01_First")
    h.sub(first, 2, sample("MS01.2_B", "B"))
    a = h.sub(first, 1, sample("MS01.1_A", "A"))
    h.sub(second, 1, sample("MS02.1_C", "C"))
    res = h.tree()
    assert res.status_code == 200, res.text
    tree = WorkbenchTree.model_validate(res.json())
    assert tree.workflow_id == h.wf and tree.workflow_state.value == "ISATVON_REVIEW"
    assert [m.macro_category for m in tree.macros] == ["MS01_First", "MS02_Second"]
    rows = tree.macros[0].sub_skills
    assert [r.sub_skill.skill_metadata.skill_id for r in rows] == ["MS01.1_A", "MS01.2_B"]
    assert rows[0].id == a.id and rows[0].macro_id == first
    assert rows[0].content_sha256 == a.content_sha256  # the If-Match an edit sends back


def test_e8_7_an_invalid_sub_skill_carries_its_findings(h: TreeHarness) -> None:
    finding = {
        "rule": 5,
        "path": IDEF + "v_variables.budget_limit",
        "message": "budget_limit must be positive",
    }
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_X", "X"), state="INVALID", findings=[finding])
    (row,) = WorkbenchTree.model_validate(h.tree().json()).macros[0].sub_skills
    assert row.state.value == "INVALID"
    assert [(v.path, v.message) for v in row.validation_errors] == [
        (finding["path"], finding["message"])
    ]


def test_e8_7_a_workflow_before_extraction_is_an_empty_tree(h: TreeHarness) -> None:
    res = h.tree()
    assert res.status_code == 200 and res.json()["macros"] == []


@pytest.mark.parametrize(("state", "approved"), [("APPROVED", True), ("GENERATED", False)])
def test_e8_7_sow_approved_follows_the_workflows_sow_version(
    h: TreeHarness, state: str, approved: bool
) -> None:
    h.sow_state(state)
    assert h.tree().json()["sow_approved"] is approved


@pytest.mark.parametrize("state", ["BUILDING", "FROZEN"])
def test_e8_7_the_tree_is_readable_in_every_workflow_state(h: TreeHarness, state: str) -> None:
    with h.sessions() as s:
        s.execute(update(Workflow).where(Workflow.id == h.wf).values(state=state))
        s.commit()
    res = h.tree()
    assert res.status_code == 200 and res.json()["workflow_state"] == state


def test_e8_7_other_tenants_unassigned_and_admins_are_refused(h: TreeHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_X", "X"))
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.tree(token=other), 404, "NOT_FOUND")
    problem(h.tree(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    admin = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    problem(h.tree(token=admin), 403, "WRONG_PORTAL")
    problem(h.tree(workflow=uuid.uuid4()), 404, "NOT_FOUND")
