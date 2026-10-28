"""E8.3 `POST W/isatvon/validate` through the real app and auth chain: every Sub-Skill of the
Workflow revalidated with the extraction / build validator; read-only; tenant- and project-scoped.
"""

import copy
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.workflows.models import Workflow
from sbam_contracts.api import API_BASE
from sbam_contracts.canonical import sha256_of
from test_auth import ADMIN_A, FDE_A2, TENANT_A, TENANT_B, problem
from test_workflows_api import Harness as WorkflowsHarness

IDEF = "isatvon_definition."
MACRO = "MS01_Investor_Registration_Access"


def sample(**dotted: Any) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    raw = copy.deepcopy(raw)
    for path, value in dotted.items():
        *parents, leaf = path.split(".")
        node = raw
        for part in parents:
            node = node[part]
        node[leaf] = value
    return raw


class Harness(WorkflowsHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (MacroSkill, SubSkill):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.wf = self.workflow()
        self.macro = self.add_macro(self.wf)

    def add_macro(self, workflow_id: uuid.UUID) -> uuid.UUID:
        with self.sessions() as s:
            row = MacroSkill(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                workflow_id=workflow_id,
                sow_version_id=self.v1,
                macro_category=MACRO,
                name="Investor Registration Access",
                description="Access.",
                order=1,
                sow_refs=["M-ACCESS"],
                agent_ref=None,
                validation_errors=[],
            )
            row.state = "PENDING"
            s.add(row)
            s.commit()
            return row.id

    def add_sub_skill(
        self, isatvon: dict[str, Any], order: int = 1, state: str = "VALID"
    ) -> uuid.UUID:
        with self.sessions() as s:
            row = SubSkill(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                macro_skill_id=self.macro,
                workflow_id=self.wf,
                skill_id=isatvon["skill_metadata"]["skill_id"],
                skill_name=isatvon["skill_metadata"]["skill_name"],
                order=order,
                isatvon=isatvon,
                isatvon_framework_version="1.4",
                sow_refs=["F-ACCESS-1"],
                validation_errors=[],
                content_sha256=sha256_of(isatvon).removeprefix("sha256:"),
            )
            row.state = state
            s.add(row)
            s.commit()
            return row.id

    def validate(self, workflow_id: uuid.UUID | None = None, token: str | None = None) -> Any:
        path = f"/projects/{self.project}/workflows/{workflow_id or self.wf}/isatvon/validate"
        return self.call("POST", path, token or self.fde())

    def state_of(self, sub_skill_id: uuid.UUID) -> str:
        with self.sessions() as s:
            row = s.get(SubSkill, sub_skill_id)
            assert row is not None
            return row.state


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e8_3_validate_reports_every_sub_skill_with_coded_findings(h: Harness) -> None:
    good = h.add_sub_skill(sample(), order=1)
    tool = sample()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    broken = sample(
        **{
            "skill_metadata.skill_id": "MS01.2_Investor_KYC_Verifier",
            IDEF + "n_notification.next": None,  # the last one
            IDEF + "t_tech_stack.fixed_check_tool": tool,
        }
    )
    bad = h.add_sub_skill(broken, order=2, state="VALID")  # stored VALID, no longer is
    res = h.validate()
    assert res.status_code == 200, res.text
    report = {r["id"]: r for r in res.json()["sub_skills"]}
    assert report[str(good)] == {"id": str(good), "state": "VALID", "validation_errors": []}
    assert report[str(bad)]["state"] == "INVALID"
    assert [(e["path"], e["code"]) for e in report[str(bad)]["validation_errors"]] == [
        (IDEF + "t_tech_stack.fixed_check_tool", "TOOLS_NOT_DECOUPLED")
    ]
    assert h.state_of(bad) == "VALID"  # read-only: nothing moved


def test_e8_3_validate_keeps_later_states_as_they_are(h: Harness) -> None:
    building = h.add_sub_skill(sample(), state="BUILDING")
    (row,) = h.validate().json()["sub_skills"]
    assert (row["id"], row["state"]) == (str(building), "BUILDING")


def test_e8_3_validate_an_empty_workflow_is_an_empty_report(h: Harness) -> None:
    assert h.validate().json() == {"sub_skills": []}


def test_e8_3_validate_is_tenant_and_project_scoped(h: Harness) -> None:
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.validate(token=other), 404, "NOT_FOUND")
    problem(h.validate(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")  # unassigned
    admin = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    problem(h.validate(token=admin), 403, "WRONG_PORTAL")
    problem(h.validate(workflow_id=uuid.uuid4()), 404, "NOT_FOUND")


def test_e8_3_a_workflow_of_another_project_is_404(h: Harness) -> None:
    with h.sessions() as s:
        row = s.get(Workflow, h.wf)
        assert row is not None
        row.project_id = uuid.uuid4()
        s.commit()
    problem(h.validate(), 404, "NOT_FOUND")


def test_e8_3_validate_is_in_openapi_as_a_post(h: Harness) -> None:
    spec = h.client.get(f"{API_BASE}/openapi.json").json()
    ops = spec["paths"][
        f"{API_BASE}/projects/{{project_id}}/workflows/{{workflow_id}}/isatvon/validate"
    ]
    assert set(ops) == {"post"}
