"""E7.2 scope change through the real app and auth chain: `POST P/sow/new-version`, the impact
preview `GET P/sow/versions/{v}/impact`, and the approval that stores the same impact. The SOW is
the MS01.1 one (module M-ACCESS, features F-ACCESS-1 / F-ACCESS-2); v1's Workflow has a Macro
Skill and three Sub-Skills citing those features. The Temporal client is the approval tests' fake.
"""

import copy
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import MacroSkill, SubSkill, SubSkillEdit
from sbam_api.projects.models import Project
from sbam_api.sow.models import SowImpactRecord, SowVersion
from sbam_api.sow.tiptap import to_tiptap
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import PipelineStage
from sqlalchemy import select
from test_auth import TENANT_A, TENANT_B, problem
from test_workflows_api import Harness as WorkflowsHarness

REASON = "Add refunds"


class Harness(WorkflowsHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (MacroSkill, SubSkill, SubSkillEdit, SowImpactRecord):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]

    # -- the world ---------------------------------------------------------------------------
    def approved_v1(self, sub_skills: list[list[str]] | None = None) -> uuid.UUID:
        """v1 APPROVED, the project past Workflow creation, v1's Workflow with one Macro Skill
        and a Sub-Skill per `sow_refs` list (default: F-ACCESS-1, F-ACCESS-1, F-ACCESS-2)."""
        assert self.approve(self.sha()).status_code == 200
        self.set_project_state("WORKFLOW_CREATED")
        self.wf = self.workflow()
        macro = self.add_macro(["M-ACCESS"])
        for order, refs in enumerate(
            sub_skills or [["F-ACCESS-1"], ["F-ACCESS-1"], ["F-ACCESS-2"]], start=1
        ):
            self.add_sub_skill(macro, order, refs)
        return self.wf

    def set_project_state(self, state: str) -> None:
        with self.sessions() as s:
            project = s.get(Project, uuid.UUID(self.project))
            assert project is not None
            project.state = state
            s.commit()

    def add_macro(self, refs: list[str]) -> uuid.UUID:
        with self.sessions() as s:
            row = MacroSkill(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                workflow_id=self.wf,
                sow_version_id=self.v1,
                macro_category="MS01_Investor_Registration_Access",
                name="Investor Registration Access",
                description="Access.",
                order=1,
                sow_refs=refs,
                agent_ref=None,
                validation_errors=[],
            )
            row.state = "PENDING"
            s.add(row)
            s.commit()
            return row.id

    def add_sub_skill(self, macro: uuid.UUID, order: int, refs: list[str]) -> uuid.UUID:
        with self.sessions() as s:
            row = SubSkill(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                macro_skill_id=macro,
                workflow_id=self.wf,
                skill_id=f"MS01.{order}_Skill_{order}",
                skill_name=f"Skill {order}",
                order=order,
                isatvon={},
                isatvon_framework_version="1.4",
                sow_refs=refs,
                validation_errors=[],
                content_sha256="0" * 64,
            )
            row.state = "CERTIFIED"
            s.add(row)
            s.commit()
            return row.id

    # -- calls -------------------------------------------------------------------------------
    def new_version(self, reason: Any = REASON, token: str | None = None) -> Any:
        return self.call(
            "POST",
            f"/projects/{self.project}/sow/new-version",
            token or self.fde(),
            json={"reason": reason},
        )

    def impact(self, version: int, token: str | None = None) -> Any:
        return self.call(
            "GET", f"/projects/{self.project}/sow/versions/{version}/impact", token or self.fde()
        )

    def save_doc(self, doc: dict[str, Any]) -> int:
        """Save `doc` onto the current draft; the new version's number."""
        current = self.get("").json()["current"]["version"]
        res = self.save(to_tiptap(doc), base=current)
        assert res.status_code == 201, res.text
        return int(res.json()["version"])

    def doc(self, **changes: Any) -> dict[str, Any]:
        """v1's content with `section__field` / `features__0__name` style changes."""
        doc = copy.deepcopy(self.structured)
        for path, value in changes.items():
            node: Any = doc
            *parents, last = path.split("__")
            for part in parents:
                node = node[int(part)] if part.isdigit() else node[part]
            node[last] = value
        return doc

    def project_state(self) -> str:
        with self.sessions() as s:
            project = s.get(Project, uuid.UUID(self.project))
            assert project is not None
            return project.state

    def version_row(self, number: int) -> SowVersion:
        return self.row(number)

    def stored_impact(self, version_id: uuid.UUID) -> SowImpactRecord | None:
        with self.sessions() as s:
            row = s.scalars(
                select(SowImpactRecord).where(SowImpactRecord.sow_version_id == version_id)
            ).one_or_none()
            s.expunge_all()
            return row


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def skill_ids(report: dict[str, Any]) -> set[str]:
    return {s["skill_id"] for s in report["affected_sub_skills"]}


# --- new version -----------------------------------------------------------------------------


def test_e7_2_t01_a_draft_is_made_from_the_approved_version(h: Harness) -> None:
    h.approved_v1()
    res = h.new_version()
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["version"], body["status"], body["source"]) == (2, "IN_REVIEW", "EDITED")
    assert body["parent_version"] == 1
    assert body["sha256"] == h.version_row(1).sha256  # the same content
    assert res.headers["ETag"] == h.get("").json()["etag"]
    assert h.get("").json()["current"]["version"] == 2
    assert h.project_state() == "SOW_REVIEW"
    with h.sessions() as s:
        audit = s.scalars(select(AuditLog).where(AuditLog.action == "SOW_NEW_VERSION")).one()
    assert audit.after == {"version": 2, "reason": REASON}
    assert audit.actor_id == h.version_row(2).created_by  # the FDE who started it


def test_e7_2_t02_without_an_approved_version_it_is_409(h: Harness) -> None:
    problem(h.new_version(), 409, "NO_APPROVED_SOW")


@pytest.mark.parametrize("reason", ["", "x" * 501])
def test_e7_2_t06_the_reason_is_required_and_bounded(h: Harness, reason: str) -> None:
    h.approved_v1()
    body = problem(h.new_version(reason), 422, "VALIDATION_ERROR")
    assert any(e["path"].endswith("reason") for e in body["errors"])


def test_e7_2_t08_a_second_draft_is_409(h: Harness) -> None:
    h.approved_v1()
    assert h.new_version().status_code == 201
    body = problem(h.new_version(), 409, "DRAFT_EXISTS")
    assert "v2" in body["detail"]


def test_e7_2_the_draft_is_edited_like_any_version(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    v3 = h.save_doc(h.doc(features__0__name="Investor OTP & refunds"))
    assert v3 == 3 and h.version_row(3).parent_version_id == h.version_row(2).id


# --- impact ------------------------------------------------------------------------------------


def test_e7_2_t03_only_the_sub_skills_citing_the_changed_feature_are_affected(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    v3 = h.save_doc(h.doc(features__0__name="Investor OTP & refunds"))
    res = h.impact(v3)
    assert res.status_code == 200, res.text
    report = res.json()
    assert (report["version"], report["base_version"]) == (3, 1)
    assert skill_ids(report) == {"MS01.1_Skill_1", "MS01.2_Skill_2"}
    assert {s["reason"] for s in report["affected_sub_skills"]} == {"F-ACCESS-1 MODIFIED"}
    assert report["reused_sub_skills_count"] == 1
    assert [m["macro_category"] for m in report["affected_macro_skills"]] == [
        "MS01_Investor_Registration_Access"
    ]
    assert [s["section_id"] for s in report["changed_sections"]] == ["features"]
    assert report["added_modules"] == []


def test_e7_2_a_change_only_in_assumptions_invalidates_nothing(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    points = copy.deepcopy(h.structured["assumptions_open_points"])
    points["open_points"] = [*points.get("open_points", []), "Refund policy to be confirmed"]
    v3 = h.save_doc(h.doc(assumptions_open_points=points))
    report = h.impact(v3).json()
    assert report["affected_sub_skills"] == [] and report["affected_macro_skills"] == []
    assert report["reused_sub_skills_count"] == 3
    assert [s["section_id"] for s in report["changed_sections"]] == ["assumptions_open_points"]


def test_e7_2_a_sub_skill_citing_two_features_is_affected_when_one_changes(h: Harness) -> None:
    h.approved_v1(sub_skills=[["F-ACCESS-1", "F-ACCESS-2"], ["F-ACCESS-1"]])
    h.new_version()
    second = copy.deepcopy(h.structured["features"][1])
    second["name"] = "Investor KYC & AML"
    features = [h.structured["features"][0], second]
    report = h.impact(h.save_doc(h.doc(features=features))).json()
    assert skill_ids(report) == {"MS01.1_Skill_1"}
    assert report["reused_sub_skills_count"] == 1


def test_e7_2_a_removed_feature_invalidates_what_cites_it(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    report = h.impact(h.save_doc(h.doc(features=h.structured["features"][:1]))).json()
    assert skill_ids(report) == {"MS01.3_Skill_3"}
    assert report["affected_sub_skills"][0]["reason"] == "F-ACCESS-2 REMOVED"


def test_e7_2_impact_needs_a_draft_derived_from_the_approved_version(h: Harness) -> None:
    problem(h.impact(1), 409, "NO_APPROVED_SOW")  # nothing approved yet
    h.approved_v1()
    problem(h.impact(1), 409, "NOT_IN_REVIEW")  # the approved version itself
    problem(h.impact(99), 404, "NOT_FOUND")


def test_e7_2_t07_another_tenant_gets_404(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.impact(2, token=other), 404, "NOT_FOUND")
    problem(h.new_version(token=other), 404, "NOT_FOUND")


# --- approval ------------------------------------------------------------------------------------


def test_e7_2_t05_the_stored_impact_equals_the_preview(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    v3 = h.save_doc(h.doc(features__0__name="Investor OTP & refunds"))
    preview = h.impact(v3).json()
    res = h.approve(h.sha(v3), version=v3)
    assert res.status_code == 200, res.text
    stored = h.stored_impact(h.version_row(v3).id)
    assert stored is not None
    assert stored.report == preview
    assert stored.base_version_id == h.version_row(1).id and stored.workflow_id == h.wf
    with h.sessions() as s:
        cited = {
            str(r.id) for r in s.scalars(select(SubSkill)).all() if r.skill_id in skill_ids(preview)
        }
    assert set(stored.affected_sub_skill_ids) == cited
    assert h.version_row(1).state == "SUPERSEDED" and h.version_row(v3).state == "APPROVED"


def test_e7_2_approving_while_the_workflow_builds_is_409_and_stores_nothing(h: Harness) -> None:
    wf = h.approved_v1()
    h.new_version()
    v3 = h.save_doc(h.doc(features__0__name="Investor OTP & refunds"))
    for state in ("BUILDING", "CERTIFYING"):
        with h.sessions() as s:
            row = s.get(Workflow, wf)
            assert row is not None
            row.state = state
            s.commit()
        problem(h.approve(h.sha(v3), version=v3), 409, "WORKFLOW_BUILDING")
    assert h.stored_impact(h.version_row(v3).id) is None
    assert h.version_row(1).state == "APPROVED"


def test_e7_2_a_first_approval_stores_no_impact(h: Harness) -> None:
    assert h.approve(h.sha()).status_code == 200
    with h.sessions() as s:
        assert s.scalars(select(SowImpactRecord)).all() == []


def test_e7_2_a_new_module_is_reported_for_a_new_macro_skill(h: Harness) -> None:
    h.approved_v1()
    h.new_version()
    module = copy.deepcopy(h.structured["modules"][0]) | {"id": "M-REFUNDS", "name": "Refunds"}
    feature = copy.deepcopy(h.structured["features"][0]) | {
        "id": "F-REFUNDS-1",
        "module_id": "M-REFUNDS",
        "name": "Refund check",
    }
    doc = h.doc(
        modules=[*h.structured["modules"], module], features=[*h.structured["features"], feature]
    )
    report = h.impact(h.save_doc(doc)).json()
    assert report["added_modules"] == ["M-REFUNDS"]
    assert report["affected_sub_skills"] == [] and report["reused_sub_skills_count"] == 3


# --- 2a: a scope-change approval replaces the pipeline run --------------------------------------


def scope_change(h: Harness) -> int:
    h.approved_v1()
    h.new_version()
    return h.save_doc(h.doc(features__0__name="Investor OTP & refunds"))


def test_e7_2_a_waiting_run_is_replaced_by_one_carrying_the_approval(h: Harness) -> None:
    v3 = scope_change(h)
    h.pipeline.stage = PipelineStage.WAIT_GENERATE  # v1's Workflow waits for Generate
    signals = len(h.pipeline.signals)
    assert h.approve(h.sha(v3), version=v3).status_code == 200
    assert len(h.pipeline.terminated) == 1
    (started,) = h.pipeline.started
    assert started.resume.stage is PipelineStage.WAIT_SOW_APPROVAL
    assert started.resume.sow_approved.sow_version_id == h.version_row(v3).id
    assert started.resume.sow_approved.sha256 == h.sha(v3)
    assert len(h.pipeline.signals) == signals  # nothing signalled to the old run


def test_e7_2_an_ended_run_is_not_terminated_just_replaced(h: Harness) -> None:
    v3 = scope_change(h)
    h.pipeline.stage, h.pipeline.open = PipelineStage.DONE, False
    assert h.approve(h.sha(v3), version=v3).status_code == 200
    assert h.pipeline.terminated == [] and len(h.pipeline.started) == 1


def test_e7_2_a_working_run_is_409_and_nothing_is_approved(h: Harness) -> None:
    v3 = scope_change(h)
    h.pipeline.stage = PipelineStage.EXTRACT_SUB_SKILLS
    problem(h.approve(h.sha(v3), version=v3), 409, "PIPELINE_RUNNING")
    assert h.version_row(v3).state == "IN_REVIEW" and h.version_row(1).state == "APPROVED"
    assert h.stored_impact(h.version_row(v3).id) is None
    assert h.pipeline.terminated == [] and h.pipeline.started == []


def test_e7_2_temporal_down_at_the_restart_is_503_and_nothing_is_approved(h: Harness) -> None:
    v3 = scope_change(h)
    h.pipeline.down = True
    problem(h.approve(h.sha(v3), version=v3), 503, "ORCHESTRATOR_UNAVAILABLE")
    assert h.version_row(v3).state == "IN_REVIEW"
    assert h.stored_impact(h.version_row(v3).id) is None
