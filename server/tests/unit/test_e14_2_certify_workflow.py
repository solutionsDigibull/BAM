"""E14.2: `certify_macros`, `validate_workflow` and `certify_workflow` against a real (SQLite)
database: what they load, the MACRO_SKILL / WORKFLOW certificates and Agent transitions they write,
and everything that must stop them. The rules themselves are tested in
test_e14_2_workflow_checks.py; schema.sql is read from an in-memory storage fake."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import pipeline_sessions
from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationLevel, CertificationRow
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.workflows.models import Workflow
from sbam_contracts.canonical import sha256_of
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.pipeline import WorkflowRef
from sbam_workers.build.dag import Node
from sbam_workers.build.workflow_manifest import Agent, DraftInputs, Skill, draft
from sbam_workers.certify.workflow import (
    CERTIFICATION_REFUSED,
    VALIDATION_ACTION,
    VALIDATION_FAILED,
    SqlFacts,
    WorkflowCertification,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
DIGEST = "sha256:" + "34" * 32
SCHEMA_KEY = "t/p/w/code/schema"
FULL_SQL = (
    "CREATE TABLE investors (id uuid PRIMARY KEY, email text NOT NULL);\n"
    "CREATE TABLE quotes (id uuid PRIMARY KEY, amount numeric);\n"
)
# skill_id -> (macro, declared tables, next)
SKILLS: dict[str, tuple[str, list[str], str | None]] = {
    "MS01.1_Login": ("MS01_Access", ["investors"], "MS01.2_Verify"),
    "MS01.2_Verify": ("MS01_Access", [], "MS02_Quotes"),
    "MS02.1_Quote": ("MS02_Quotes", ["quotes"], None),
}


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, key: str, *, verify: bool = False) -> bytes:
        return self.objects[key]


def isatvon(skill_id: str) -> dict[str, Any]:
    _, tables, nxt = SKILLS[skill_id]
    return {
        "skill_metadata": {"skill_id": skill_id},
        "isatvon_definition": {
            "v_variables": {"tables": tables},
            "n_notification": {"next": nxt},
        },
    }


class World:
    """A Workflow in CERTIFYING with 2 Macros and 3 Sub-Skills, each certified on its bundle, a
    manifest draft over those bundles and the stored schema.sql; each test spoils one thing."""

    def __init__(
        self, sql: str = FULL_SQL, isatvon_of: Callable[[str], dict[str, Any]] = isatvon
    ) -> None:
        self.isatvon_of = isatvon_of
        self.sessions: sessionmaker[Session] = pipeline_sessions()
        bind = self.sessions.kw["bind"]
        for table in (
            Workflow,
            MacroSkill,
            SubSkill,
            CodeAttempt,
            CertificationRow,
            WorkflowManifestRow,
        ):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.tenant, self.project, self.workflow = uuid.uuid4(), uuid7(), uuid7()
        self.ctx = TenantContext(self.tenant, self.project)
        self.storage = FakeStorage()
        self.inp = WorkflowRef(
            tenant_id=self.tenant,
            project_id=self.project,
            run_id=uuid.uuid4(),
            correlation_id="cid-e14-2",
            workflow_id=self.workflow,
        )
        self.macros: dict[str, uuid.UUID] = {}
        self.subs: dict[str, uuid.UUID] = {}
        with self.sessions() as s:
            wf = Workflow(
                id=self.workflow,
                tenant_id=self.tenant,
                project_id=self.project,
                sow_version_id=uuid7(),
                major=1,
                version="1.0.0",
                name="Investor onboarding",
                created_by="SYSTEM",
            )
            wf.state = "CERTIFYING"
            s.add(wf)
            for order, category in enumerate(("MS01_Access", "MS02_Quotes"), start=1):
                macro = MacroSkill(
                    id=uuid7(),
                    tenant_id=self.tenant,
                    project_id=self.project,
                    workflow_id=self.workflow,
                    sow_version_id=wf.sow_version_id,
                    macro_category=category,
                    name=category,
                    description="-",
                    order=order,
                    sow_refs=[],
                    agent_ref=None,
                    validation_errors=[],
                )
                macro.state = "PENDING"
                s.add(macro)
                self.macros[category] = macro.id
            s.commit()
        for order, skill_id in enumerate(SKILLS, start=1):
            self.subs[skill_id] = self.add_sub(skill_id, order)
            self.add_bundle(skill_id, self.bundle_of(skill_id, 1))
            self.certify_sub(skill_id, self.bundle_of(skill_id, 1))
        self.store_sql(sql)
        self.write_manifest()

    # --- seeding --------------------------------------------------------------------------------

    @staticmethod
    def bundle_of(skill_id: str, n: int) -> str:
        return hashlib.sha256(f"{skill_id}:{n}".encode()).hexdigest()

    def add_sub(self, skill_id: str, order: int, state: str = "CERTIFIED") -> uuid.UUID:
        with self.sessions() as s:
            row = SubSkill(
                id=uuid7(),
                tenant_id=self.tenant,
                project_id=self.project,
                macro_skill_id=self.macros[SKILLS[skill_id][0]],
                workflow_id=self.workflow,
                skill_id=skill_id,
                skill_name=skill_id,
                order=order,
                isatvon=self.isatvon_of(skill_id),
                isatvon_framework_version="1.4",
                sow_refs=[],
                validation_errors=[],
                content_sha256="0" * 64,
            )
            row.state = state
            s.add(row)
            s.commit()
            return row.id

    def set_sub_state(self, skill_id: str, state: str) -> None:
        with self.sessions() as s:
            row = s.get(SubSkill, self.subs[skill_id])
            assert row is not None
            row.state = state  # test setup only
            s.commit()

    def add_bundle(self, skill_id: str, bundle: str, attempt: int = 0) -> None:
        with self.sessions() as s:
            s.add(
                CodeAttempt(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    workflow_id=self.workflow,
                    sub_skill_id=self.subs[skill_id],
                    build_no=1,
                    cycle=0,
                    attempt=attempt,
                    template_name="fastmcp-loopy",
                    template_version="1.0.1",
                    template_sha256="0" * 64,
                    status="ASSEMBLED",
                    bodies={},
                    llm_call_ids=[],
                    trace_id="t",
                    findings=[],
                    bundle_sha256=bundle,
                    created_by="sbam-worker",
                )
            )
            s.commit()

    def certify_sub(self, skill_id: str, bundle: str) -> None:
        with self.sessions() as s:
            s.add(
                CertificationRow(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    level="SUB_SKILL",
                    subject_id=self.subs[skill_id],
                    content_sha256=f"sha256:{bundle}",
                    evidence={},
                    certified_at=NOW,
                )
            )
            s.commit()

    def store_sql(self, sql: str) -> None:
        data = sql.encode()
        self.storage.objects[SCHEMA_KEY] = data
        self.schema_sha = hashlib.sha256(data).hexdigest()

    def manifest(self, bundle: int = 1) -> WorkflowManifest:
        skills = [
            Skill(
                node=Node(self.subs[sid], sid, SKILLS[sid][0], self.isatvon_of(sid)),
                manifest_sha256="sha256:" + "a" * 64,
                bundle_sha256=self.bundle_of(sid, bundle),
            )
            for sid in SKILLS
        ]
        return draft(
            DraftInputs(
                ids={
                    "tenant_id": str(self.tenant),
                    "project_id": str(self.project),
                    "workflow_id": str(self.workflow),
                },
                name="Investor onboarding",
                objective="Onboard investors.",
                version="1.0.0",
                previous_version=None,
                provenance={
                    "sow_version_id": "s",
                    "sow_sha256": "ab" * 32,
                    "approved_at": "2026-10-01T10:00:00Z",
                    "approved_by": "fde",
                    "knowledge_version_id": "k",
                    "okf_sha256": "cd" * 32,
                    "master_snapshot_sha256": "sha256:" + "ef" * 32,
                    "sow_refs": ["M-ACCESS"],
                },
                agents=[Agent("MS01_Access", "Access", None), Agent("MS02_Quotes", "Quotes", None)],
                skills=skills,
                schema_sql=(SCHEMA_KEY, self.schema_sha),
                runtime_digest=DIGEST,
            )
        )

    def write_manifest(self, manifest: WorkflowManifest | None = None, build_no: int = 1) -> str:
        done = manifest or self.manifest()
        with self.sessions() as s:
            s.add(
                WorkflowManifestRow(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    workflow_id=self.workflow,
                    build_no=build_no,
                    manifest=done.dump(),
                    sha256=done.integrity.content_sha256,
                )
            )
            s.commit()
        self.manifest_sha = done.integrity.content_sha256
        return self.manifest_sha

    # --- the stages -----------------------------------------------------------------------------

    def stages(self) -> WorkflowCertification:
        return WorkflowCertification(
            self.sessions, SqlFacts(self.storage), clock=lambda: NOW  # type: ignore[arg-type]
        )

    def certify_macros(self) -> list[uuid.UUID]:
        return asyncio.run(self.stages().run_certify_macros(self.inp)).ids

    def validate(self) -> uuid.UUID:
        return asyncio.run(self.stages().run_validate_workflow(self.inp)).id

    def certify_workflow(self) -> uuid.UUID:
        return asyncio.run(self.stages().run_certify_workflow(self.inp)).id

    # --- reading back ---------------------------------------------------------------------------

    def certificates(self, level: CertificationLevel) -> list[CertificationRow]:
        with self.sessions() as s:
            query = select(CertificationRow).where(CertificationRow.level == level.value)
            return list(s.scalars(query))

    def macro_state(self, category: str) -> str:
        with self.sessions() as s:
            row = s.get(MacroSkill, self.macros[category])
            assert row is not None
            return row.state

    def audit(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


def failure(call: Any) -> ApplicationError:
    with pytest.raises(ApplicationError) as err:
        call()
    assert err.value.non_retryable, err.value
    return err.value


# --- the happy path -------------------------------------------------------------------------------


def test_e14_2_happy_path_two_macros_and_the_workflow_certify() -> None:
    w = World()
    ids = w.certify_macros()
    macros = w.certificates(CertificationLevel.MACRO_SKILL)
    assert sorted(ids) == sorted(c.id for c in macros) and len(macros) == 2
    access = next(c for c in macros if c.subject_id == w.macros["MS01_Access"])
    expected = sha256_of(
        [
            {"skill_id": sid, "content_sha256": f"sha256:{w.bundle_of(sid, 1)}"}
            for sid in ("MS01.1_Login", "MS01.2_Verify")
        ]
    )
    assert access.content_sha256 == expected
    assert [c["skill_id"] for c in access.evidence["children"]] == ["MS01.1_Login", "MS01.2_Verify"]
    assert {w.macro_state(m) for m in w.macros} == {"CERTIFIED"}
    # the Agent machine moved through the service, audited
    assert [(a.after or {})["state"] for a in w.audit("build_started")] == ["BUILDING", "BUILDING"]
    assert len(w.audit("all_sub_skills_certified")) == 2

    with w.sessions() as s:
        newest = s.scalars(select(WorkflowManifestRow)).one()
    assert w.validate() == newest.id

    cert_id = w.certify_workflow()
    [cert] = w.certificates(CertificationLevel.WORKFLOW)
    assert cert.id == cert_id
    assert (cert.subject_id, cert.content_sha256) == (w.workflow, w.manifest_sha)
    assert sorted(m["certification_id"] for m in cert.evidence["macros"]) == sorted(map(str, ids))
    assert [s["skill_id"] for s in cert.evidence["skills"]] == list(SKILLS)
    assert cert.evidence["skills"][0]["bundle_sha256"] == w.bundle_of("MS01.1_Login", 1)
    assert cert.evidence["skills"][0]["deterministic_hash"] == "sha256:" + "a" * 64
    assert cert.evidence["schema_sql_sha256"] == w.schema_sha


def test_e14_2_the_workflow_certificate_is_what_commit_rechecks() -> None:
    """Commit and the freeze look for a WORKFLOW certificate on the newest manifest's sha256."""
    from sbam_api.certifications.repository import CertificationRepository

    w = World()
    w.certify_macros()
    w.certify_workflow()
    with w.sessions() as s:
        newest = s.scalars(select(WorkflowManifestRow)).one()
        found = CertificationRepository(s, w.ctx).find(
            CertificationLevel.WORKFLOW.value, w.workflow, newest.sha256
        )
    assert found is not None


def test_e14_2_each_activity_is_idempotent_on_retry() -> None:
    w = World()
    first = w.certify_macros()
    assert sorted(w.certify_macros()) == sorted(first)
    assert len(w.certificates(CertificationLevel.MACRO_SKILL)) == 2
    assert len(w.audit("all_sub_skills_certified")) == 2  # no second transition
    assert w.validate() == w.validate()
    cert = w.certify_workflow()
    assert w.certify_workflow() == cert
    assert len(w.certificates(CertificationLevel.WORKFLOW)) == 1


# --- what blocks ----------------------------------------------------------------------------------


def test_e14_2_t02_one_failing_sub_skill_blocks_its_macro_and_the_workflow() -> None:
    w = World()
    w.set_sub_state("MS02.1_Quote", "NEEDS_FDE_REVIEW")
    [only] = w.certify_macros()
    [cert] = w.certificates(CertificationLevel.MACRO_SKILL)
    assert cert.id == only and cert.subject_id == w.macros["MS01_Access"]
    assert w.macro_state("MS01_Access") == "CERTIFIED"
    assert w.macro_state("MS02_Quotes") == "PENDING"  # a blocked Macro stays as it is
    error = failure(w.validate)
    assert error.type == VALIDATION_FAILED
    assert error.message == "workflow validation failed: SUB_SKILL_NOT_CERTIFIED:MS02.1_Quote"
    [logged] = w.audit(VALIDATION_ACTION)
    assert logged.entity_id == str(w.workflow)
    assert logged.after is not None
    assert logged.after["findings"] == [
        {
            "check": "SUB_SKILL_NOT_CERTIFIED",
            "subject": "MS02.1_Quote",
            "message": "state is NEEDS_FDE_REVIEW, not CERTIFIED",
        }
    ]
    assert failure(w.certify_workflow).type == CERTIFICATION_REFUSED
    assert w.certificates(CertificationLevel.WORKFLOW) == []


def test_e14_2_t03_a_cycle_fails_validation_with_dag_cycle() -> None:
    w = World()
    data = w.manifest().dump()
    data["dag"]["edges"] += [  # data edges are build dependencies: they must not loop
        {"from": "MS01.1_Login", "to": "MS01.2_Verify", "kind": "data", "field": "otp"},
        {"from": "MS01.2_Verify", "to": "MS01.1_Login", "kind": "data", "field": "token"},
    ]
    w.write_manifest(WorkflowManifest.sealed(data), build_no=2)
    w.certify_macros()
    error = failure(w.validate)
    assert error.type == VALIDATION_FAILED
    assert "DAG_CYCLE:" in str(error) and "MS01.1_Login" in str(error)
    assert failure(w.certify_workflow).type == CERTIFICATION_REFUSED


def test_e14_2_a_next_edge_back_into_the_dag_is_not_a_cycle() -> None:
    """A `next` loop (which the build accepts) does not fail validation; only data edges do."""
    w = World()
    data = w.manifest().dump()
    data["dag"]["edges"].append({"from": "MS02.1_Quote", "to": "MS01.1_Login", "kind": "next"})
    w.write_manifest(WorkflowManifest.sealed(data), build_no=2)
    w.certify_macros()
    with w.sessions() as s:
        newest = s.scalars(select(WorkflowManifestRow).where(WorkflowManifestRow.build_no == 2))
        assert w.validate() == newest.one().id


def test_e14_2_t06_a_declared_table_missing_from_schema_sql_fails_validation() -> None:
    w = World(sql="CREATE TABLE investors (id uuid PRIMARY KEY, email text NOT NULL);\n")
    w.certify_macros()
    error = failure(w.validate)
    assert error.message == "workflow validation failed: SCHEMA_TABLE_MISSING:MS02.1_Quote"
    assert "CREATE" not in str(error)  # never SQL text


def test_e14_2_a_retried_failed_validation_writes_one_audit_row() -> None:
    w = World()
    w.set_sub_state("MS01.2_Verify", "NEEDS_FDE_REVIEW")
    failure(w.validate)
    failure(w.validate)
    assert len(w.audit(VALIDATION_ACTION)) == 1


def test_e14_2_a_certificate_on_an_old_bundle_blocks() -> None:
    """MS01.2 was rebuilt (bundle 2) after its certificate (bundle 1): its Macro and the Workflow
    are blocked even though the state still says CERTIFIED."""
    w = World()
    w.add_bundle("MS01.2_Verify", w.bundle_of("MS01.2_Verify", 2), attempt=1)
    w.certify_macros()
    assert [c.subject_id for c in w.certificates(CertificationLevel.MACRO_SKILL)] == [
        w.macros["MS02_Quotes"]
    ]
    assert w.macro_state("MS01_Access") == "PENDING"
    error = failure(w.validate)
    assert error.message == "workflow validation failed: SUB_SKILL_NOT_CERTIFIED:MS01.2_Verify"


def test_e14_2_an_empty_workflow_never_certifies() -> None:
    w = World()
    with w.sessions() as s:
        for model in (CodeAttempt, SubSkill):  # certificates are frozen
            for row in s.scalars(select(model)):
                s.delete(row)
        s.commit()
    assert w.certify_macros() == []
    assert w.certificates(CertificationLevel.MACRO_SKILL) == []
    assert failure(w.validate).type == VALIDATION_FAILED
    assert failure(w.certify_workflow).type == CERTIFICATION_REFUSED
    assert w.certificates(CertificationLevel.WORKFLOW) == []


def test_e14_2_certify_workflow_refuses_when_validation_would_fail() -> None:
    """The Macros certified, then a Sub-Skill changed: certify_workflow re-runs the verdict."""
    w = World()
    w.certify_macros()
    w.validate()
    w.set_sub_state("MS01.1_Login", "BUILDING")
    error = failure(w.certify_workflow)
    assert error.type == CERTIFICATION_REFUSED
    assert "SUB_SKILL_NOT_CERTIFIED:MS01.1_Login" in str(error)
    assert w.certificates(CertificationLevel.WORKFLOW) == []


def test_e14_2_certify_workflow_needs_every_macro_certificate() -> None:
    w = World()
    error = failure(w.certify_workflow)  # certify_macros never ran
    assert error.message == (
        "workflow certification refused: MACRO_NOT_CERTIFIED:MS01_Access, "
        "MACRO_NOT_CERTIFIED:MS02_Quotes"
    )


def test_e14_2_certify_workflow_refuses_a_manifest_on_old_bundles() -> None:
    w = World()
    w.write_manifest(w.manifest(bundle=2), build_no=2)  # names bundles nobody built
    w.certify_macros()
    error = failure(w.certify_workflow)
    assert "MANIFEST_STALE:MS01.1_Login" in str(error)


def test_e14_2_a_macro_with_no_sub_skill_fails_validation_not_certification() -> None:
    """certify_macros skips a Macro with no child and the DAG checks only count skill nodes, so the
    refusal must come from validate_workflow (which has a retry path), not certify_workflow (which
    would leave the Workflow in CERTIFYING)."""
    w = World()
    with w.sessions() as s:
        empty = MacroSkill(
            id=uuid7(),
            tenant_id=w.tenant,
            project_id=w.project,
            workflow_id=w.workflow,
            sow_version_id=uuid7(),
            macro_category="MS03_Empty",
            name="MS03_Empty",
            description="-",
            order=3,
            sow_refs=[],
            agent_ref=None,
            validation_errors=[],
        )
        empty.state = "PENDING"
        s.add(empty)
        s.commit()
    w.certify_macros()
    error = failure(w.validate)
    assert error.type == VALIDATION_FAILED
    assert error.message == "workflow validation failed: MACRO_NOT_CERTIFIED:MS03_Empty"
    assert len(w.audit(VALIDATION_ACTION)) == 1


def test_e14_2_a_stale_manifest_fails_validation_not_certification() -> None:
    w = World()
    w.write_manifest(w.manifest(bundle=2), build_no=2)  # names bundles nobody built
    w.certify_macros()
    error = failure(w.validate)
    assert error.type == VALIDATION_FAILED
    assert "MANIFEST_STALE:MS01.1_Login" in error.message


def test_e14_2_a_workflow_outside_the_run_is_refused() -> None:
    w = World()
    w.inp = w.inp.model_copy(update={"project_id": uuid7()})
    assert failure(w.certify_macros).type == "BuildIdsMismatch"


# --- what Commit and the #132 freeze read back ---------------------------------------------------


def test_e14_2_a_certified_workflow_passes_the_checks_the_freeze_requires_of_it() -> None:
    """`OrderedFreezer.eligible` refuses unless every Commit check but SECRETS passes. The two this
    issue produces, WORKFLOW_CERTIFIED and SUB_SKILLS_CERTIFIED, pass on what the three stages
    wrote (MCP_SERVER and UI_BUNDLE are other stages' evidence)."""
    from sbam_api.workflow_commit import checks

    w = World()
    w.certify_macros()
    w.validate()
    w.certify_workflow()
    with w.sessions() as s:  # the pipeline applies workflow_certified after certify_workflow
        workflow = s.get(Workflow, w.workflow)
        assert workflow is not None
        assert checks._certified(s, w.ctx, workflow, w.manifest_sha) is not None  # CERTIFYING
        workflow.state = "CERTIFIED"  # test setup: the transition is the pipeline's
        s.commit()
        assert checks._certified(s, w.ctx, workflow, w.manifest_sha) is None
        assert checks._sub_skills(s, w.ctx, w.workflow) is None


def test_e14_2_the_freeze_only_applies_freeze_to_a_macro_certify_macros_certified() -> None:
    """#132's interim `_MACRO_STEPS` brings a PENDING / BUILDING Macro up itself; on a Macro
    certify_macros already moved to CERTIFIED it must only apply `freeze` (no illegal event)."""
    from sbam_api.versioning import numbers
    from sbam_api.versioning.freezer import OrderedFreezer

    w = World()
    w.certify_macros()
    with w.sessions() as s:
        subs = list(s.scalars(select(SubSkill)))
        OrderedFreezer.freeze_children(
            s, w.ctx, w.workflow, subs, numbers.parse("1.0.0"), "cid-e14-2-freeze"
        )
        s.commit()
    assert {w.macro_state(m) for m in w.macros} == {"FROZEN"}
    assert len(w.audit("all_sub_skills_certified")) == 2  # certify_macros' only, none from freeze
    assert len(w.audit("build_started")) == 2
    with w.sessions() as s:
        macro_ids = [str(m) for m in w.macros.values()]
        rows = s.scalars(
            select(AuditLog).where(AuditLog.entity_type == "agent", AuditLog.action == "freeze")
        )
        assert sorted(r.entity_id for r in rows if r.entity_id in macro_ids) == sorted(macro_ids)


# --- field-level checks on full ISATVON Sub-Skills ------------------------------------------------

ISA14 = Path(__file__).parents[1] / "fixtures/isa14/ms01_1_structured.json"
HANDOFF_SQL = FULL_SQL + "CREATE TABLE auth_event_audit_handoff (id bigserial PRIMARY KEY);\n"


def full_isatvon(
    skill_id: str,
    inputs: list[str],
    outputs: dict[str, Any],
    parameters: list[dict[str, Any]],
    external: list[str] | None = None,
) -> dict[str, Any]:
    """The MS01.1 reference Sub-Skill, renamed into the World and given these S and O sides."""
    data: dict[str, Any] = json.loads(ISA14.read_text("utf-8"))
    macro, tables, nxt = SKILLS[skill_id]
    data["skill_metadata"] |= {"skill_id": skill_id, "macro_category": macro}
    definition = data["isatvon_definition"]
    definition["s_source"]["required_inputs"] = inputs
    if external is not None:
        definition["s_source"]["external_data"] = [
            {"name": name, "kind": "API", "ref": f"partner/{name}"} for name in external
        ]
    definition["o_outcome"]["json_schema"] = outputs
    definition["v_variables"] |= {"tables": tables, "parameters": parameters}
    definition["n_notification"]["next"] = nxt
    return data


def typed_world(amount_out: str) -> World:
    """MS01.1 outputs `amount` as `amount_out`; MS01.2 takes `amount` as a number."""
    io: dict[str, tuple[list[str], dict[str, Any], list[dict[str, Any]]]] = {
        "MS01.1_Login": (["email"], {"amount": amount_out}, []),
        "MS01.2_Verify": (
            ["amount"],
            {"investor_id": "string"},
            [{"name": "amount", "type": "number"}],
        ),
        "MS02.1_Quote": (["investor_id"], {"quote": "number"}, []),
    }
    return World(sql=HANDOFF_SQL, isatvon_of=lambda sid: full_isatvon(sid, *io[sid]))


def test_e14_2_t05_an_integer_output_feeds_a_number_input() -> None:
    w = typed_world("integer")
    w.certify_macros()
    assert w.validate() is not None
    assert w.audit(VALIDATION_ACTION) == []


def test_e14_2_t05_a_string_output_into_a_number_input_fails_validation() -> None:
    w = typed_world("string")
    w.certify_macros()
    error = failure(w.validate)
    assert error.type == VALIDATION_FAILED
    assert error.message == "workflow validation failed: INTERFACE_INCOMPATIBLE:MS01.2_Verify"
    [row] = w.audit(VALIDATION_ACTION)
    assert row.after is not None
    assert row.after["findings"] == [
        {
            "check": "INTERFACE_INCOMPATIBLE",
            "subject": "MS01.2_Verify",
            "message": "MS01.1_Login outputs amount as string; "
            "MS01.2_Verify takes amount as number",
        }
    ]
    assert failure(w.certify_workflow).type == CERTIFICATION_REFUSED
