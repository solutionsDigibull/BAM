"""E13.2 `GET W/sandbox-runs/{run_id}/logs` through the real app and auth chain, and the rule that a
client can never pick the sandbox mode.

The logs are what the code under test printed. They are served as a signed attachment, and a run is
only visible to the tenant, project and Workflow it belongs to: every other caller gets the same 404
as for a made-up id, and the probe leaves an `access_denied` audit row.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_api.audit.models import AuditLog
from sbam_api.audit.security import AuditLogSecurityAudit
from sbam_api.builds.models import CodeAttempt
from sbam_api.core.ids import uuid7
from sbam_api.core.ratelimit import AuditThrottle
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.api import API_BASE
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy import select
from test_auth import TENANT_A, TENANT_B, problem
from test_build_retry_api import URL, FakeBuilds, client, fde_user, key, status
from test_workflows_api import Harness as WorkflowHarness

SIGNING_KEY = "k" * 40
LOGS = b"--- stdout ---\nhello\n--- stderr ---\n"


class Harness(WorkflowHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (CodeAttempt, SandboxRunRecord):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        state = self.client.app.state  # type: ignore[attr-defined]
        state.settings.storage_signing_key = SecretStr(SIGNING_KEY)
        state.storage = None
        self.storage = LocalDiskBackend(tmp_path)
        self.client.follow_redirects = False  # the 302 is the answer
        # the harness logs denials; this one writes them to `audit_log` like the deployed app
        state.auth.audit = AuditLogSecurityAudit(lambda: self.sessions, AuditThrottle(30))
        self.workflow_id = self.workflow()
        self.attempt = self.code_attempt(self.workflow_id)

    def code_attempt(self, workflow_id: uuid.UUID) -> uuid.UUID:
        with self.sessions() as s:
            row = CodeAttempt(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                workflow_id=workflow_id,
                sub_skill_id=uuid7(),
                build_no=1,
                cycle=0,
                attempt=0,
                template_name="t",
                template_version="1",
                template_sha256="0" * 64,
                status="ASSEMBLED",
                bodies={},
                trace_id="tr",
                findings=[],
            )
            s.add(row)
            s.commit()
            return row.id

    def run(self, attempt: uuid.UUID | None = None, *, logs: bytes | None = LOGS) -> uuid.UUID:
        key_: str | None = None
        if logs is not None:
            scope = StorageScope(TENANT_A, uuid.UUID(self.project), self.workflow_id)
            key_ = self.storage.put(logs, scope, Kind.TESTS, "text/plain").storage_key
        with self.sessions() as s:
            row = SandboxRunRecord(
                id=uuid7(),
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                code_attempt_id=attempt or self.attempt,
                dataset_id=uuid.uuid4(),
                run_no=1,
                mode="CUBESANDBOX",
                status="FINISHED",
                runtime_digest="sha256:" + "a" * 64,
                logs_key=key_,
                truncated=False,
                is_frozen=True,
                temporal_workflow_id="wf",
                activity_id="act",
                activity_attempt=1,
                started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                finished_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                created_by="test",
            )
            s.add(row)
            s.commit()
            return row.id

    def logs(self, run: uuid.UUID, token: str | None = None, workflow: Any = None) -> Any:
        path = f"/projects/{self.project}/workflows/{workflow or self.workflow_id}"
        return self.call("GET", f"{path}/sandbox-runs/{run}/logs", token or self.fde())

    def denials(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "access_denied")))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e13_2_the_logs_of_a_run_are_a_302_to_a_signed_attachment(h: Harness) -> None:
    run = h.run()
    res = h.logs(run)
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    assert location.startswith("/api/v1/files/") and "sig=" in location and "exp=" in location
    served = h.client.get(location, headers={"Authorization": f"Bearer {h.fde()}"})
    assert served.status_code == 200 and served.content == LOGS
    assert served.headers["content-disposition"].startswith("attachment")  # never rendered
    assert served.headers["x-content-type-options"] == "nosniff"


def test_e13_2_t08_another_tenants_fde_gets_404_problem_json_and_an_audit_row(h: Harness) -> None:
    run = h.run()
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    res = h.logs(run, token=other)
    problem(res, 404, "NOT_FOUND")
    assert res.headers["content-type"].startswith("application/problem+json")
    assert len(h.denials()) == 1


def test_e13_2_an_unknown_run_and_a_run_of_another_workflow_are_the_same_404(h: Harness) -> None:
    run = h.run()
    problem(h.logs(uuid.uuid4()), 404, "NOT_FOUND")
    elsewhere = h.workflow(2, sow_version=h.generated(h.structured))
    problem(h.logs(run, workflow=elsewhere), 404, "NOT_FOUND")
    assert len(h.denials()) == 2  # a caller probing for ids leaves a trace each time


def test_e13_2_a_run_with_no_stored_logs_is_404_not_an_empty_download(h: Harness) -> None:
    problem(h.logs(h.run(logs=None)), 404, "NOT_FOUND")


def test_e13_2_logs_need_a_credential(h: Harness) -> None:
    run = h.run()
    path = f"/projects/{h.project}/workflows/{h.workflow_id}/sandbox-runs/{run}/logs"
    problem(h.client.get(f"{API_BASE}{path}"), 401, "UNAUTHENTICATED")  # no Authorization header


def test_e13_2_t09_a_client_cannot_pick_the_sandbox_mode_on_a_retry(tmp_path: Path) -> None:
    fake = FakeBuilds(status(SubSkillState.TESTING_BLOCKED, waiting=True))
    c = client(tmp_path, fake, fde_user())
    body = {"reason": "the host is back", "mode": "LOCAL_DOCKER_NONCERT"}
    res = c.post(URL, json=body, headers=key())
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_ERROR"
    assert fake.signals == []  # nothing was signalled: the field is refused, not ignored
    ok = c.post(URL, json={"reason": "the host is back"}, headers=key(2))
    assert ok.status_code == 202
