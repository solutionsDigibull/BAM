"""E9.5: the Workflow manifest draft. `draft` and the DAG on plain inputs (T01-T05 and the edge
cases), then the `build_workflow_manifest` stage against the E9.1 world (SQLite) for idempotency
(T07) and cross-Workflow references (T06)."""

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.db.repository import StaleRowVersion, TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.manifests.repository import WorkflowManifestRepository
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_contracts.api import ErrorCode
from sbam_contracts.canonical import canonical_json, workflow_content_hash
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.pipeline import WorkflowRef
from sbam_workers.build.dag import DraftInvalid, Node, build_dag
from sbam_workers.build.manifest import IDS_MISMATCH, build_for
from sbam_workers.build.workflow_manifest import (
    Agent,
    DraftInputs,
    Skill,
    SqlWorkflowManifestStore,
    draft,
)
from sbam_workers.build.workflow_manifest import build_for as build_workflow_for
from sqlalchemy import select, update
from sqlalchemy.exc import DatabaseError
from temporalio.exceptions import ApplicationError
from test_e9_1_manifest_store import Build

SCHEMA = Path(__file__).parents[2] / "contracts/schemas/manifest/workflow_manifest.schema.json"
SCHEMA_SHA = "5c" * 32
DIGEST = "sha256:" + "34" * 32


def isatvon(inputs: list[str], outputs: list[str], nxt: str | None = None) -> dict[str, Any]:
    """The parts of a stored ISATVON Sub-Skill the DAG reads."""
    return {
        "isatvon_definition": {
            "s_source": {"required_inputs": inputs},
            "o_outcome": {"json_schema": {o: "string" for o in outputs}},
            "n_notification": {"next": nxt},
        }
    }


def skill(
    skill_id: str,
    inputs: list[str],
    outputs: list[str],
    nxt: str | None = None,
    *,
    hash_: str = "a",
    bundle: str | None = None,
) -> Skill:
    category = "MS0" + skill_id[3] + ("_Access" if skill_id.startswith("MS01") else "_Quotes")
    return Skill(
        node=Node(
            uuid.uuid5(uuid.NAMESPACE_OID, skill_id),
            skill_id,
            category,
            isatvon(inputs, outputs, nxt),
        ),
        manifest_sha256="sha256:" + hash_ * 64,
        bundle_sha256=bundle,
    )


def inputs(skills: list[Skill] | None = None, **changes: Any) -> DraftInputs:
    values: dict[str, Any] = {
        "ids": {"tenant_id": "t", "project_id": "p", "workflow_id": "w"},
        "name": "Investor onboarding",
        "objective": "Onboard investors.",
        "version": "1.0.0",
        "previous_version": None,
        "provenance": {
            "sow_version_id": "s",
            "sow_sha256": "ab" * 32,
            "approved_at": "2026-10-01T10:00:00Z",
            "approved_by": "fde",
            "knowledge_version_id": "k",
            "okf_sha256": "cd" * 32,
            "master_snapshot_sha256": "sha256:" + "ef" * 32,
            "sow_refs": ["M-ACCESS"],
        },
        "agents": [Agent("MS01_Access", "Access", None), Agent("MS02_Quotes", "Quotes", None)],
        "skills": skills if skills is not None else three(),
        "schema_sql": ("tenant/p/build/sql", SCHEMA_SHA),
        "runtime_digest": DIGEST,
    }
    return DraftInputs(**(values | changes))


def three() -> list[Skill]:
    """2 Agents, 3 Sub-Skills: MS01.1 -> MS01.2 (otp), MS01.2 -> MS02.1 (investor_id)."""
    return [
        skill("MS01.1_Login", ["email"], ["otp"], "MS01.2_Verify"),
        skill("MS01.2_Verify", ["otp"], ["investor_id"], "MS02_Quotes", hash_="b"),
        skill("MS02.1_Quote", ["investor_id", "rfq_file"], ["quote"], None, hash_="c"),
    ]


def test_e9_5_t01_the_draft_is_valid_against_the_workflow_manifest_schema() -> None:
    manifest = draft(inputs())
    jsonschema.validate(manifest.dump(), json.loads(SCHEMA.read_text("utf-8")))
    assert manifest.certification is None  # never certified or frozen here
    assert manifest.integrity.content_sha256 == workflow_content_hash(manifest.dump())
    access = manifest.macro_skills[0]
    assert [s.skill_id for s in access.skills] == ["MS01.1_Login", "MS01.2_Verify"]
    assert access.certification_id is None and access.skills[0].bundle_sha256 is None
    assert manifest.schema_sql.sha256 == SCHEMA_SHA


def test_e9_5_t02_database_order_does_not_change_the_bytes() -> None:
    first = draft(inputs())
    shuffled = three()
    shuffled.reverse()
    agents = list(reversed(inputs().agents))
    second = draft(inputs(shuffled, agents=agents))
    assert canonical_json(first.dump()) == canonical_json(second.dump())
    assert first.integrity == second.integrity


def test_e9_5_t03_a_dependency_cycle_is_dag_cycle_with_its_path() -> None:
    looped = [
        skill("MS01.2_Verify", ["investor_id"], ["otp"]),
        skill("MS02.1_Quote", ["otp"], ["investor_id"], hash_="c"),
    ]
    with pytest.raises(DraftInvalid) as raised:
        draft(inputs(looped))
    assert raised.value.code is ErrorCode.DAG_CYCLE and raised.value.kind == "DagCycle"
    path = str(raised.value).split(": ", 1)[1].split(" -> ")
    assert path[0] == path[-1] and set(path) == {"MS01.2_Verify", "MS02.1_Quote"}


@pytest.mark.parametrize("index", [0, 2])
def test_e9_5_t04_a_skill_hash_change_changes_only_the_content_hash(index: int) -> None:
    before = draft(inputs())
    changed = three()
    changed[index] = Skill(changed[index].node, "sha256:" + "f" * 64, None)
    after = draft(inputs(changed))
    assert after.integrity != before.integrity
    assert after.dag == before.dag and after.schema_sql == before.schema_sql


def test_e9_5_t04_a_bundle_or_schema_change_changes_the_content_hash() -> None:
    before = draft(inputs()).integrity
    built = three()
    built[1] = Skill(built[1].node, built[1].manifest_sha256, "9" * 64)
    assert draft(inputs(built)).integrity != before
    assert draft(inputs(schema_sql=("tenant/p/build/sql", "6d" * 32))).integrity != before


def test_e9_5_t05_no_passed_schema_is_schema_sql_missing() -> None:
    with pytest.raises(DraftInvalid) as raised:
        draft(inputs(schema_sql=None))
    assert (
        raised.value.code is ErrorCode.SCHEMA_SQL_MISSING
        and raised.value.kind == "SchemaSqlMissing"
    )


def test_e9_5_the_dag_has_contains_next_and_data_edges_and_no_edge_for_external_inputs() -> None:
    graph = build_dag([s.node for s in three()])
    kinds = {(e["from"], e["to"], e["kind"]) for e in graph.edges}
    assert ("MS01_Access", "MS01.1_Login", "contains") in kinds
    assert ("MS01.1_Login", "MS01.2_Verify", "next") in kinds
    assert ("MS01.2_Verify", "MS02_Quotes", "next") in kinds  # next Agent
    assert ("MS01.2_Verify", "MS02.1_Quote", "data") in kinds
    # email and rfq_file come from outside (an external S source): no edge
    assert not any(e.get("field") in {"email", "rfq_file"} for e in graph.edges)
    login, verify, quote = (s.node.sub_skill_id for s in three())
    assert graph.depends_on == {verify: [login], quote: [verify]}


def test_e9_5_a_single_agent_with_one_sub_skill_is_a_valid_draft() -> None:
    one = [skill("MS01.1_Login", ["email"], ["otp"])]
    manifest = draft(inputs(one, agents=[Agent("MS01_Access", "Access", "AG-1")]))
    assert [len(a.skills) for a in manifest.macro_skills] == [1]
    assert manifest.dag.edges == [{"from": "MS01_Access", "to": "MS01.1_Login", "kind": "contains"}]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"agents": [Agent("Access", "Access", None)]}, ErrorCode.ISATVON_CONTRACT),
        (
            {"agents": [*inputs().agents, Agent("MS03_Empty", "Empty", None)]},
            ErrorCode.MACRO_REQUIRES_SUB_SKILL,
        ),
        (
            {"agents": [Agent("MS01_Access", "A", None), Agent("MS01_Access", "B", None)]},
            ErrorCode.ISATVON_CONTRACT,
        ),
    ],
)
def test_e9_5_the_agent_set_is_checked(change: dict[str, Any], code: ErrorCode) -> None:
    with pytest.raises(DraftInvalid) as raised:
        draft(inputs(**change))
    assert raised.value.code is code


def test_e9_5_every_sub_skill_needs_a_manifest() -> None:
    missing = three()
    missing[0] = Skill(missing[0].node, None, None)
    with pytest.raises(DraftInvalid, match="no manifest"):
        draft(inputs(missing))


def test_e9_5_a_certified_manifest_is_not_written_here() -> None:
    data = draft(inputs()).dump()
    assert "certification" not in data or data["certification"] is None
    with pytest.raises(ValueError, match="certified without"):
        WorkflowManifest.sealed(
            data | {"certification": {"state": "FROZEN", "certified_at": "2026-10-01T00:00:00Z"}}
        )


# --- the stage, against the database ----------------------------------------------------------


class Stage(Build):
    """The E9.1 world with its Sub-Skill's manifest built and a PASSED schema.sql."""

    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp)
        engine = self.p.w.sessions.kw["bind"]
        WorkflowManifestRow.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        build_for(self.store, self.inp())
        self.ref = WorkflowRef(**self.p.inp.model_dump(), workflow_id=self.p.workflow)
        self.workflows = SqlWorkflowManifestStore(self.p.w.sessions)

    def schema(self, **values: Any) -> None:
        with self.p.w.sessions() as s:
            s.add(
                WorkflowSchema(
                    **{
                        "tenant_id": self.p.inp.tenant_id,
                        "project_id": self.p.w.project,
                        "workflow_id": self.p.workflow,
                        "version": 1,
                        "source": "GENERATED",
                        "storage_key": "t/p/build/schema.sql",
                        "sha256": SCHEMA_SHA,
                        "tables": [],
                        "status": "PASSED",
                        "findings": [],
                        "created_by": "SYSTEM",
                    }
                    | values
                )
            )
            s.commit()

    def rows(self) -> list[WorkflowManifestRow]:
        with self.p.w.sessions() as s:
            rows = list(s.scalars(select(WorkflowManifestRow)))
            s.expunge_all()
            return rows


@pytest.fixture
def stage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stage]:
    yield Stage(tmp_path_factory.mktemp("s"))


def test_e9_5_t07_a_retried_activity_writes_one_row_with_the_same_hash(stage: Stage) -> None:
    stage.schema()
    first = build_workflow_for(stage.workflows, stage.ref)
    second = build_workflow_for(stage.workflows, stage.ref)
    [row] = stage.rows()
    assert first == second and row.id == first.id
    assert (row.build_no, row.is_frozen) == (1, False)
    manifest = WorkflowManifest.model_validate(row.manifest)
    assert row.sha256 == manifest.integrity.content_sha256 == manifest.computed_hash()
    assert manifest.ids.workflow_id == str(stage.p.workflow)
    assert manifest.provenance.okf_sha256 == "cd" * 32
    assert manifest.runtime_image_digest == DIGEST
    assert manifest.macro_skills[0].skills[0].bundle_sha256 is None  # no codegen yet


def test_e9_5_t05_the_stage_without_a_passed_schema_fails_non_retryable(stage: Stage) -> None:
    stage.schema(status="FAILED")
    with pytest.raises(ApplicationError) as raised:
        build_workflow_for(stage.workflows, stage.ref)
    assert raised.value.type == "SchemaSqlMissing" and raised.value.non_retryable
    assert stage.rows() == []


def test_e9_5_t06_a_sub_skill_of_another_workflow_is_rejected_and_audited(stage: Stage) -> None:
    stage.schema()
    with stage.p.w.sessions() as s:  # its Agent belongs to another tenant's Workflow
        s.execute(update(SubSkill).values(macro_skill_id=uuid.uuid4()))
        s.commit()
    with pytest.raises(ApplicationError) as raised:
        build_workflow_for(stage.workflows, stage.ref)
    assert raised.value.type == IDS_MISMATCH and raised.value.non_retryable
    assert stage.rows() == []
    with stage.p.w.sessions() as s:
        actions = s.scalars(select(AuditLog.action)).all()
    assert "WORKFLOW_MANIFEST_REF_REJECTED" in actions


def test_e9_5_a_stale_update_of_the_draft_is_412_and_a_frozen_one_can_not_change(
    stage: Stage,
) -> None:
    stage.schema()
    build_workflow_for(stage.workflows, stage.ref)
    ctx = TenantContext(stage.p.inp.tenant_id, stage.p.w.project)
    with stage.p.w.sessions() as s:
        repo = WorkflowManifestRepository(s, ctx)
        [row] = repo.list()
        row.is_frozen = True
        with pytest.raises(StaleRowVersion):
            repo.update(row, expected_row_version=row.row_version + 1)
        s.rollback()
        [row] = repo.list()
        row.is_frozen = True
        repo.update(row, expected_row_version=row.row_version)
        s.commit()
    with stage.p.w.sessions() as s, pytest.raises(DatabaseError):
        s.execute(update(WorkflowManifestRow).values(build_no=2))
        s.commit()
