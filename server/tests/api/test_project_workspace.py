"""E4.1 project workspace API: pipeline start, the pipeline dashboard and the read-only workflows.

Same stand-ins as the upload tests (SQLite with schema `sbam` mapped away, a fake Temporal client).
The real-Temporal half (T09) is in tests/temporal/test_project_start_api.py; T07 against a stopped
Compose Temporal is not run here, the unreachable-server case below stands in for it.
"""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from runs_db import FakeClient, FakeHandle
from sbam_api.audit.models import AuditLog
from sbam_api.pipeline.timeline import build_counts, build_steps
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.api import API_BASE
from sbam_contracts.state_machines import DocumentVersionState as D
from sqlalchemy import select
from test_auth import FDE_A1, FDE_A2, FDE_B1, TENANT_B, problem
from test_uploads import Harness as UploadsHarness


class Harness(UploadsHarness):
    def start(self, project: str | None = None, token: str | None = None, **headers: str) -> Any:
        return self.call(
            "POST",
            f"/projects/{project or self.project}/pipeline/start",
            token or self.fde(),
            headers=headers,
        )

    def count_runs(self) -> int:
        return self.count(TemporalRun)

    def state(self) -> str:
        [project] = [p for p in self.projects() if str(p.id) == self.project]
        return str(project.state)

    def trail(self, entity_type: str, action: str | None = None) -> list[AuditLog]:
        with self.sessions() as s:
            query = select(AuditLog).where(
                AuditLog.entity_type == entity_type, AuditLog.entity_id == self.project
            )
            if action:
                query = query.where(AuditLog.action == action)
            return list(s.scalars(query.order_by(AuditLog.id)))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def workflow_id(h: Harness) -> str:
    return f"pipeline-{h.project}"


# --- start ---------------------------------------------------------------------------------------


def test_e4_1_t01_assigned_fde_starts_the_pipeline(h: Harness) -> None:
    h.seed_versions(2)
    res = h.start()
    assert res.status_code == 202, res.text
    run = res.json()["run"]
    assert run["temporal_workflow_id"] == workflow_id(h)
    assert run["type"] == "ProjectPipeline" and run["current_stage"] == "ingest"
    assert h.state() == "INGESTING"
    assert list(h.temporal.executions) == [workflow_id(h)]  # one execution, "running"
    call = h.temporal.calls[0]
    assert call["id_conflict_policy"].name == "FAIL" and call["task_queue"] == "sbam-ingest"
    arg = call["arg"]
    assert str(arg.project_id) == h.project and arg.actor_id == str(FDE_A1)
    assert len(arg.document_version_ids) == 2  # the project's documents, from the database
    assert h.count_runs() == 1


def test_e4_1_start_audits_the_actor_and_the_run(h: Harness) -> None:
    h.seed_versions(1)
    run = h.start().json()["run"]
    [started] = h.trail("project", "PIPELINE_STARTED")
    assert started.actor_id == str(FDE_A1)
    assert started.after is not None and started.after["run_id"] == run["id"]
    [transition] = h.trail("project_pipeline", "start_processing")  # through the state machine
    assert transition.actor_id == str(FDE_A1)
    assert transition.before == {"state": "IDLE", "row_version": 1}
    assert transition.after is not None and transition.after["state"] == "INGESTING"
    key = f"{run['temporal_run_id']}:project_pipeline:start_processing:0"  # the workflow's key
    assert transition.idempotency_key == key


def test_e4_1_start_ignores_a_body_naming_other_documents(h: Harness) -> None:
    h.seed_versions(1)
    stranger = str(uuid.uuid4())
    res = h.call(
        "POST",
        f"/projects/{h.project}/pipeline/start",
        h.fde(),
        json={"document_version_ids": [stranger]},
    )
    assert res.status_code == 202, res.text
    ids = {str(i) for i in h.temporal.calls[0]["arg"].document_version_ids}
    assert stranger not in ids and len(ids) == 1


def test_e4_1_t02_starting_twice_is_409_pipeline_running(h: Harness) -> None:
    h.seed_versions(1)
    assert h.start().status_code == 202
    problem(h.start(), 409, "PIPELINE_RUNNING")
    assert len(h.temporal.calls) == 1  # the second request never reached Temporal
    assert h.count_runs() == 1 and h.state() == "INGESTING"


def test_e4_1_t02_a_running_workflow_without_a_matching_state_is_still_409(h: Harness) -> None:
    """Two requests past the state check: Temporal's FAIL policy answers the loser."""
    h.seed_versions(1)
    h.temporal.executions[workflow_id(h)] = "run-1"
    problem(h.start(), 409, "PIPELINE_RUNNING")
    assert h.state() == "IDLE" and h.count_runs() == 0
    assert h.trail("project", "PIPELINE_STARTED") == []


def test_e4_1_t03_concurrent_start_gives_one_202_and_one_409(h: Harness) -> None:
    h.seed_versions(1)

    class Gated(FakeClient):
        """The winner's start returns only after the other request has been answered."""

        def __init__(self) -> None:
            super().__init__()
            self.gate = asyncio.Event()
            self.entered = asyncio.Event()

        async def start_workflow(self, workflow: str, arg: Any, **kwargs: Any) -> FakeHandle:
            handle = await super().start_workflow(workflow, arg, **kwargs)  # registers or raises
            self.entered.set()
            await self.gate.wait()
            return handle

    gated = Gated()
    h.client.app.state.temporal = gated  # type: ignore[attr-defined]
    path = f"{API_BASE}/projects/{h.project}/pipeline/start"
    headers = {"authorization": f"Bearer {h.fde()}"}

    async def race() -> list[httpx.Response]:
        # The shared in-memory SQLite connection is not safe for two threads at once, so request B
        # starts only when A is parked inside Temporal's start (A is then not touching the DB).
        transport = httpx.ASGITransport(app=h.client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
            a = asyncio.ensure_future(api.post(path, headers=headers))
            await asyncio.wait_for(gated.entered.wait(), 20)
            b = await asyncio.wait_for(api.post(path, headers=headers), 20)
            gated.gate.set()
            return [await a, b]

    first, second = asyncio.run(race())
    assert (first.status_code, second.status_code) == (202, 409), (first.text, second.text)
    assert second.json()["code"] == "PIPELINE_RUNNING"
    assert len(gated.executions) == 1 and h.count_runs() == 1
    assert h.state() == "INGESTING"
    assert len(h.trail("project", "PIPELINE_STARTED")) == 1


def test_e4_1_t04_same_idempotency_key_returns_the_same_run(h: Harness) -> None:
    h.seed_versions(1)
    first = h.start(**{"Idempotency-Key": "click-1"})
    second = h.start(**{"Idempotency-Key": "click-1"})
    assert first.status_code == second.status_code == 202
    assert first.json()["run"]["id"] == second.json()["run"]["id"]
    assert len(h.temporal.calls) == 1 and h.count_runs() == 1
    assert len(h.trail("project", "PIPELINE_STARTED")) == 1
    # a different key is a different request: the pipeline is running now
    problem(h.start(**{"Idempotency-Key": "click-2"}), 409, "PIPELINE_RUNNING")


def test_e4_1_idempotency_key_limits(h: Harness) -> None:
    h.seed_versions(1)
    assert h.start(**{"Idempotency-Key": "k" * 129}).status_code == 422
    assert h.state() == "IDLE" and h.temporal.calls == []
    assert h.start(**{"Idempotency-Key": "k" * 128}).status_code == 202


def test_e4_1_idempotency_key_is_per_project(h: Harness) -> None:
    h.seed_versions(1)
    other = h.create("Second Bot")["id"]
    h.members.assigned[FDE_A1].add(uuid.UUID(other))
    assert h.start(**{"Idempotency-Key": "same"}).status_code == 202
    # another project with the same key is not a replay of the first project's start
    res = h.start(project=other, **{"Idempotency-Key": "same"})
    problem(res, 409, "NO_DOCUMENTS")


def test_e4_1_t05_no_active_documents_is_409_no_documents(h: Harness) -> None:
    problem(h.start(), 409, "NO_DOCUMENTS")
    assert h.state() == "IDLE" and h.temporal.calls == []
    h.seed_versions(2, D.REJECTED.value)  # a rejected file is not a document to process
    problem(h.start(), 409, "NO_DOCUMENTS")
    assert h.temporal.calls == []


def test_e4_1_t06_unassigned_fde_gets_404_and_nothing_starts(h: Harness) -> None:
    h.seed_versions(1)
    problem(h.start(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    assert h.temporal.calls == [] and h.state() == "IDLE"
    assert h.start(token="not-a-token").status_code == 401  # noqa: S106


def test_e4_1_t06_other_tenants_fde_gets_404(h: Harness) -> None:
    h.seed_versions(1)
    h.members.assigned[FDE_B1] = {uuid.UUID(h.project)}  # even if a stale assignment names it
    token = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    problem(h.start(token=token), 404, "NOT_FOUND")
    assert h.temporal.calls == [] and h.state() == "IDLE"


def test_e4_1_start_needs_the_fde_role(h: Harness) -> None:
    h.seed_versions(1)
    assert h.start(token=h.admin_a()).status_code == 403
    assert h.temporal.calls == []


def test_e4_1_t07_temporal_down_is_503_and_state_stays_idle(h: Harness) -> None:
    h.seed_versions(1)
    h.temporal.down = True
    problem(h.start(), 503, "TEMPORAL_UNAVAILABLE")
    assert h.state() == "IDLE"
    assert h.count_runs() == 0 and h.trail("project", "PIPELINE_STARTED") == []
    assert h.trail("project_pipeline", "start_processing") == []
    h.temporal.down = False  # back up: the same click now works
    assert h.start().status_code == 202 and h.state() == "INGESTING"


def test_e4_1_t07_unreachable_temporal_server_is_503(h: Harness) -> None:
    h.seed_versions(1)
    h.client.app.state.temporal = None  # type: ignore[attr-defined]
    h.client.app.state.settings.temporal_host = "127.0.0.1:1"  # type: ignore[attr-defined]
    problem(h.start(), 503, "TEMPORAL_UNAVAILABLE")
    assert h.state() == "IDLE"


def test_e4_1_archived_between_page_load_and_start_is_409(h: Harness) -> None:
    h.seed_versions(1)
    assert h.call("POST", f"/admin/projects/{h.project}/archive", h.admin_a()).status_code == 200
    problem(h.start(), 409, "PROJECT_ARCHIVED")
    assert h.temporal.calls == []


def test_e4_1_restart_after_ingestion_failed(h: Harness) -> None:
    h.seed_versions(1)
    h.set_state(h.project, "INGESTION_FAILED")
    res = h.start()
    assert res.status_code == 202, res.text
    assert h.state() == "INGESTING"
    [transition] = h.trail("project_pipeline", "retry")  # the failed state's own event
    assert transition.before is not None and transition.before["state"] == "INGESTION_FAILED"
    assert transition.idempotency_key is not None
    assert transition.idempotency_key.endswith(":project_pipeline:start_processing:0")


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("KNOWLEDGE_BUILDING", "PIPELINE_RUNNING"),
        ("SOW_REVIEW", "PIPELINE_RUNNING"),
        ("KNOWLEDGE_FAILED", "INVALID_TRANSITION"),
        ("CANCELLED", "INVALID_TRANSITION"),
        ("WORKFLOW_CREATED", "INVALID_TRANSITION"),
    ],
)
def test_e4_1_other_states_are_refused(h: Harness, state: str, code: str) -> None:
    h.seed_versions(1)
    h.set_state(h.project, state)
    problem(h.start(), 409, code)
    assert h.temporal.calls == [] and h.state() == state


# --- dashboard -----------------------------------------------------------------------------------


def test_e4_1_t01_dashboard_state_steps_and_counts(h: Harness) -> None:
    for state in (D.UPLOADED, D.PARSED, D.PARSED, D.REJECTED, D.PARSE_FAILED, D.DELETED):
        h.seed_versions(1, state.value)
    idle = h.call("GET", f"/projects/{h.project}/pipeline", h.fde())
    assert idle.status_code == 200, idle.text
    body = idle.json()
    assert body["state"] == "IDLE"
    assert body["counts"] == {"uploaded": 1, "parsed": 2, "rejected": 1, "failed": 1}
    assert [s["status"] for s in body["steps"]] == ["pending"] * 6
    assert body["steps"][0]["name"] == "INGESTING"

    h.set_state(h.project, "KNOWLEDGE_BUILDING")  # state equals projects.pipeline_state
    body = h.call("GET", f"/projects/{h.project}/pipeline", h.fde()).json()
    assert body["state"] == "KNOWLEDGE_BUILDING"
    assert [s["status"] for s in body["steps"]] == ["done", "running"] + ["pending"] * 4
    assert set(body["steps"][0]) == {"name", "status", "started_at", "ended_at"}


def test_e4_1_dashboard_timestamps_come_from_the_audit_trail(h: Harness) -> None:
    h.seed_versions(1)
    h.start()
    body = h.call("GET", f"/projects/{h.project}/pipeline", h.fde()).json()
    ingesting = body["steps"][0]
    assert body["state"] == "INGESTING" and ingesting["status"] == "running"
    assert ingesting["started_at"] is not None and ingesting["ended_at"] is None


def test_e4_1_dashboard_is_404_for_unassigned_and_other_tenants(h: Harness) -> None:
    path = f"/projects/{h.project}/pipeline"
    problem(h.call("GET", path, h.realm.token(FDE_A2)), 404, "NOT_FOUND")
    h.members.assigned[FDE_B1] = {uuid.UUID(h.project)}
    other = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    problem(h.call("GET", path, other), 404, "NOT_FOUND")
    assert h.call("GET", path, h.admin_a()).status_code == 403
    assert h.client.get(f"{API_BASE}{path}").status_code == 401


# --- workflows: read-only ------------------------------------------------------------------------


def test_e4_1_t09_no_workflow_before_the_sow_is_approved(h: Harness) -> None:
    res = h.call("GET", f"/projects/{h.project}/workflows", h.fde())
    assert res.status_code == 200 and res.json() == {"items": [], "next_cursor": None}
    problem(
        h.call("GET", f"/projects/{h.project}/workflows", h.realm.token(FDE_A2)), 404, "NOT_FOUND"
    )


def test_e4_1_t08_no_route_creates_a_workflow(h: Harness) -> None:
    spec = h.client.get(f"{API_BASE}/openapi.json").json()
    paths = {p: ops for p, ops in spec["paths"].items() if p.endswith("/workflows")}
    assert list(paths) == [f"{API_BASE}/projects/{{project_id}}/workflows"]
    assert set(paths[next(iter(paths))]) == {"get"}  # no post / put / patch / delete
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        res = h.call(method, f"/projects/{h.project}/workflows", h.fde(), json={})
        assert res.status_code == 405, (method, res.text)
    admin = h.call("POST", f"/admin/projects/{h.project}/workflows", h.admin_a(), json={})
    assert admin.status_code == 404  # nor does an admin route exist


# --- timeline and counts units -------------------------------------------------------------------


def test_e4_1_counts_ignore_deleted_and_purged() -> None:
    counts = build_counts(
        {"UPLOADED": 1, "SCANNING": 1, "PARSING": 1, "PARSED": 4, "SCAN_FAILED": 2, "DELETED": 9}
    )
    assert counts.model_dump() == {"uploaded": 3, "parsed": 4, "rejected": 0, "failed": 2}


def test_e4_1_failed_step_and_cancelled_pipeline() -> None:
    from sbam_contracts.state_machines import ProjectPipelineState as P

    failed = build_steps(P.INGESTION_FAILED, [])
    assert [s.status.value for s in failed] == ["failed"] + ["pending"] * 5
    cancelled = build_steps(P.CANCELLED, [])
    assert [s.status.value for s in cancelled] == ["cancelled"] + ["pending"] * 5
    created = build_steps(P.WORKFLOW_CREATED, [])
    assert [s.status.value for s in created] == ["done"] * 6
