"""E5.2 POST /projects/{project_id}/knowledge/rebuild through the real app and auth chain."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_api.knowledge.models import KnowledgeSource, KnowledgeVersion
from sbam_api.projects.models import Project
from sqlalchemy import select, update
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_project_workspace import Harness as WorkspaceHarness


class Harness(WorkspaceHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (KnowledgeVersion, KnowledgeSource):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]

    def rebuild(self, token: str | None = None, **headers: str) -> Any:
        return self.call(
            "POST",
            f"/projects/{self.project}/knowledge/rebuild",
            token or self.fde(),
            headers=headers,
        )

    def add_version(self, number: int, state: str) -> uuid.UUID:
        with self.sessions() as s:
            row = KnowledgeVersion(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                version=number,
                run_id=uuid7(),
                cognee_dataset=f"sbam_{TENANT_A}_{self.project}",
                coverage={},
                state=state,
            )
            s.add(row)
            s.commit()
            return row.id

    def knowledge_versions(self) -> list[tuple[int, str, uuid.UUID]]:
        with self.sessions() as s:
            rows = s.scalars(select(KnowledgeVersion).order_by(KnowledgeVersion.version))
            return [(r.version, r.state, r.run_id) for r in rows]

    def set_pipeline_state(self, state: str) -> None:
        with self.sessions() as s:
            s.execute(
                update(Project).where(Project.id == uuid.UUID(self.project)).values(state=state)
            )
            s.commit()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e5_2_t06_rebuild_is_202_and_allocates_the_next_version(h: Harness) -> None:
    h.add_version(1, "READY")
    res = h.rebuild()
    assert res.status_code == 202
    run = res.json()["run"]
    assert run["type"] == "KnowledgeRebuild"
    assert run["temporal_workflow_id"] == f"knowledge-{h.project}"
    call = h.temporal.calls[0]
    assert call["task_queue"] == "sbam-knowledge" and call["id_conflict_policy"].name == "FAIL"
    assert call["arg"].project_id == uuid.UUID(h.project)
    v1, v2 = h.knowledge_versions()
    assert v1[:2] == (1, "READY")  # stays usable until v2 is READY
    assert v2[:2] == (2, "BUILDING") and str(v2[2]) == run["id"]
    assert [a.action for a in h.trail("project", "KNOWLEDGE_REBUILD_STARTED")] == [
        "KNOWLEDGE_REBUILD_STARTED"
    ]


def test_e5_2_t05_rebuild_while_building_is_409(h: Harness) -> None:
    h.add_version(1, "READY")
    h.add_version(2, "BUILDING")
    problem(h.rebuild(), 409, "KNOWLEDGE_BUILDING")
    assert len(h.knowledge_versions()) == 2 and h.temporal.calls == []


@pytest.mark.parametrize("state", ["INGESTING", "KNOWLEDGE_BUILDING"])
def test_e5_2_rebuild_while_the_pipeline_builds_is_409(h: Harness, state: str) -> None:
    h.set_pipeline_state(state)
    problem(h.rebuild(), 409, "KNOWLEDGE_BUILDING")
    assert h.knowledge_versions() == [] and h.temporal.calls == []


def test_e5_2_t07_other_tenant_and_unassigned_fde_get_404(h: Harness) -> None:
    other_tenant = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.rebuild(token=other_tenant), 404, "NOT_FOUND")
    problem(h.rebuild(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    assert h.temporal.calls == [] and h.knowledge_versions() == []


def test_e5_2_repeated_idempotency_key_answers_the_same_run(h: Harness) -> None:
    first = h.rebuild(**{"Idempotency-Key": "k-1"})
    second = h.rebuild(**{"Idempotency-Key": "k-1"})
    assert first.status_code == second.status_code == 202
    assert first.json()["run"]["id"] == second.json()["run"]["id"]
    assert len(h.knowledge_versions()) == 1 and len(h.temporal.calls) == 1


def test_e5_2_temporal_down_is_503_and_no_version(h: Harness) -> None:
    h.temporal.down = True
    problem(h.rebuild(), 503, "TEMPORAL_UNAVAILABLE")
    assert h.knowledge_versions() == []
