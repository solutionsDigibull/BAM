"""E8.1 Workflow endpoints through the real app and auth chain: GET P/workflows lists what SBAM
created, PATCH renames (and nothing else), and no route creates a Workflow."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.workflows.models import Workflow
from sbam_contracts.api import API_BASE
from sqlalchemy import select
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_sow_approve_api import Harness as SowHarness


class Harness(SowHarness):
    def workflow(
        self, major: int = 1, state: str = "ISATVON_REVIEW", sow_version: uuid.UUID | None = None
    ) -> uuid.UUID:
        """A Workflow as `create_workflow` leaves it (SYSTEM, linked to SOW v1 by default)."""
        with self.sessions() as s:
            row = Workflow(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                sow_version_id=sow_version or self.v1,
                major=major,
                version=f"{major}.0.0",
                name=f"EMS v{major}",
                created_by="SYSTEM",
            )
            row.state = state
            s.add(row)
            s.commit()
            return row.id

    def path(self, workflow_id: uuid.UUID | None = None) -> str:
        base = f"/projects/{self.project}/workflows"
        return base if workflow_id is None else f"{base}/{workflow_id}"

    def rename(
        self,
        workflow_id: uuid.UUID,
        body: Any,
        if_match: str | None = '"1"',
        token: str | None = None,
    ) -> Any:
        headers = {} if if_match is None else {"If-Match": if_match}
        return self.call(
            "PATCH", self.path(workflow_id), token or self.fde(), headers=headers, json=body
        )


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e8_1_lists_the_projects_workflows_newest_major_first(h: Harness) -> None:
    first, second = h.workflow(1), h.workflow(2, sow_version=h.generated(h.structured))
    res = h.call("GET", h.path(), h.fde())
    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["id"] for i in items] == [str(second), str(first)]
    assert items[1] | {"created_at": None} == {
        "id": str(first),
        "project_id": h.project,
        "sow_version_id": str(h.v1),
        "name": "EMS v1",
        "version": "1.0.0",
        "state": "ISATVON_REVIEW",
        "created_by": "SYSTEM",
        "created_at": None,
        "row_version": 1,
    }


def test_e8_1_t03_the_api_has_no_create_endpoint(h: Harness) -> None:
    spec = h.client.get(f"{API_BASE}/openapi.json").json()
    workflow_paths = {p: set(ops) for p, ops in spec["paths"].items() if "/workflows" in p}
    assert workflow_paths[f"{API_BASE}/projects/{{project_id}}/workflows"] == {"get"}
    assert workflow_paths[f"{API_BASE}/projects/{{project_id}}/workflows/{{workflow_id}}"] == {
        "patch"
    }
    # No create: no POST on the collection or on a Workflow. Actions below one (sub-skill retry,
    # isatvon/validate) are other routes.
    workflow = (
        f"{API_BASE}/projects/{{project_id}}/workflows",
        f"{API_BASE}/projects/{{project_id}}/workflows/{{workflow_id}}",
    )
    assert not any("post" in workflow_paths.get(p, set()) for p in workflow)
    res = h.call("POST", h.path(), h.fde(), json={"name": "Mine", "sow_version_id": str(h.v1)})
    assert res.status_code == 405
    with h.sessions() as s:
        assert s.scalars(select(Workflow)).all() == []


def test_e8_1_t07_other_tenants_and_unassigned_fdes_get_404(h: Harness) -> None:
    workflow = h.workflow()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.call("GET", h.path(), other), 404, "NOT_FOUND")
    problem(h.rename(workflow, {"name": "X"}, token=other), 404, "NOT_FOUND")
    unassigned = h.realm.token(FDE_A2)
    problem(h.call("GET", h.path(), unassigned), 404, "NOT_FOUND")
    problem(h.rename(workflow, {"name": "X"}, token=unassigned), 404, "NOT_FOUND")


def test_e8_1_a_workflow_of_another_project_is_404(h: Harness) -> None:
    workflow = h.workflow()
    with h.sessions() as s:
        row = s.get(Workflow, workflow)
        assert row is not None
        row.project_id = uuid.uuid4()
        s.commit()
    problem(h.rename(workflow, {"name": "X"}), 404, "NOT_FOUND")
    assert h.call("GET", h.path(), h.fde()).json()["items"] == []


def test_e8_1_t08_rename_only(h: Harness) -> None:
    workflow = h.workflow()
    res = h.rename(workflow, {"state": "FROZEN", "name": "X"})
    assert res.status_code == 422
    assert any(e["path"] == "state" or "state" in str(e) for e in res.json()["errors"])
    ok = h.rename(workflow, {"name": "EMS quoting"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["name"] == "EMS quoting" and ok.json()["state"] == "ISATVON_REVIEW"
    assert ok.headers["ETag"] == '"2"'
    with h.sessions() as s:
        (audit,) = s.scalars(select(AuditLog).where(AuditLog.action == "WORKFLOW_RENAMED"))
        assert audit.before == {"name": "EMS v1"} and audit.after == {"name": "EMS quoting"}


def test_e8_1_t08_rename_needs_a_current_if_match(h: Harness) -> None:
    workflow = h.workflow()
    problem(h.rename(workflow, {"name": "X"}, if_match=None), 428, "PRECONDITION_REQUIRED")
    problem(h.rename(workflow, {"name": "X"}, if_match='"7"'), 412, "PRECONDITION_FAILED")
    problem(h.rename(workflow, {"name": "  "}), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("state", ["CERTIFIED", "FROZEN"])
def test_e8_1_t08_a_certified_or_frozen_workflow_is_423(h: Harness, state: str) -> None:
    workflow = h.workflow(state=state)
    problem(h.rename(workflow, {"name": "X"}), 423, "RESOURCE_LOCKED")
    with h.sessions() as s:
        row = s.get(Workflow, workflow)
        assert row is not None and row.name == "EMS v1"
