"""E9.4: `workflow_schemas` versions and Workflow-level `gate_results` rows (SQLite)."""

from pathlib import Path

import pytest
from knowledge_db import TENANT, World
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.models import GateResultRow
from sbam_api.schema_sql.models import WorkflowSchema
from sbam_api.schema_sql.repository import WorkflowSchemaRepository
from sbam_contracts.build_dashboard import Finding, GateName, GateStatus
from sbam_contracts.schema_sql import SchemaFindingCode, SchemaSource, WorkflowSchemaSql
from sqlalchemy.exc import IntegrityError
from sub_skills_db import Pass2


@pytest.fixture
def p(tmp_path: Path) -> Pass2:
    p = Pass2(World(tmp_path))
    engine = p.w.sessions.kw["bind"]
    for table in (CodeAttempt, WorkflowSchema, GateResultRow):
        table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return p


def built(p: Pass2, sha: str, status: GateStatus = GateStatus.PASSED) -> dict[str, object]:
    return {
        "id": uuid7(),
        "tenant_id": TENANT,
        "project_id": p.w.project,
        "workflow_id": p.workflow,
        "source": SchemaSource.GENERATED,
        "storage_key": f"x/code/{sha}",
        "sha256": sha,
        "tables": ["investors"],
        "status": status,
        "findings": [],
    }


def test_e9_4_versions_count_up_and_a_retried_build_gets_its_row(p: Pass2) -> None:
    ctx = TenantContext(tenant_id=TENANT, project_id=p.w.project)
    with p.w.sessions() as s:
        repo = WorkflowSchemaRepository(s, ctx)
        first = repo.record(built(p, "a" * 64))
        again = repo.record(built(p, "a" * 64))  # the activity retried after writing
        second = repo.record(built(p, "b" * 64))
        s.commit()
        assert (first.version, again.id, second.version) == (1, first.id, 2)
        assert repo.latest(p.workflow).id == second.id  # type: ignore[union-attr]
        assert repo.latest(p.workflow, version=1).id == first.id  # type: ignore[union-attr]
        assert repo.latest(p.workflow, source=SchemaSource.UPLOADED) is None


def test_e9_4_another_tenant_sees_no_schema(p: Pass2) -> None:
    with p.w.sessions() as s:
        mine = TenantContext(tenant_id=TENANT, project_id=p.w.project)
        WorkflowSchemaRepository(s, mine).record(built(p, "a" * 64))
        s.commit()
        theirs = TenantContext(tenant_id=uuid7(), project_id=p.w.project)
        assert WorkflowSchemaRepository(s, theirs).latest(p.workflow) is None


def gate(p: Pass2, **subject: object) -> GateResultRow:
    return GateResultRow(
        id=uuid7(),
        tenant_id=TENANT,
        project_id=p.w.project,
        gate=GateName.SCHEMA_SQL,
        status=GateStatus.FAILED,
        findings=[],
        **subject,
    )


def test_e9_4_a_gate_row_is_for_a_workflow_or_a_sub_skill(p: Pass2) -> None:
    with p.w.sessions() as s:
        s.add(gate(p, workflow_id=p.workflow))  # SCHEMA_SQL: Workflow-level, no Sub-Skill
        s.commit()
        s.add(gate(p))
        with pytest.raises(IntegrityError, match="has_subject"):
            s.commit()


def test_e9_4_the_get_contract_carries_findings_with_their_statement() -> None:
    body = WorkflowSchemaSql(
        workflow_id=uuid7(),
        version=1,
        source=SchemaSource.GENERATED,
        status=GateStatus.FAILED,
        findings=[
            Finding(
                gate=GateName.SCHEMA_SQL,
                code=SchemaFindingCode.FORBIDDEN_STATEMENT,
                message="COPY ... PROGRAM is not allowed",
                line=3,
            )
        ],
    )
    assert WorkflowSchemaSql.model_validate_json(body.model_dump_json()) == body
    with pytest.raises(ValueError):
        WorkflowSchemaSql.model_validate({**body.model_dump(), "sha256": "nothex"})
