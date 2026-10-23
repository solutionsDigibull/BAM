"""E7.1 POST P/sow/versions/{v}/approve through the real app and auth chain. The Temporal client
is a fake: its `status` query says where the pipeline is, and every signal is recorded."""

import copy
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.sow.models import Sow, SowVersion
from sbam_contracts.pipeline import PipelineStage, PipelineStatus
from sqlalchemy import select, update
from temporalio.service import RPCError, RPCStatusCode
from test_auth import ADMIN_A, FDE_A1, TENANT_B, problem
from test_sow_api import Harness as SowHarness


class Handle:
    def __init__(self, client: "PipelineClient") -> None:
        self.client = client

    async def query(self, name: str, **_: Any) -> PipelineStatus:
        if self.client.down:
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        return PipelineStatus(stage=self.client.stage)

    async def signal(self, name: str, payload: Any) -> None:
        if self.client.down:
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        self.client.signals.append((name, payload))


class PipelineClient:
    def __init__(self) -> None:
        self.stage = PipelineStage.WAIT_SOW_APPROVAL
        self.down = False
        self.signals: list[tuple[str, Any]] = []

    def get_workflow_handle(self, workflow_id: str, **_: Any) -> Handle:
        return Handle(self)


class Harness(SowHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.pipeline = PipelineClient()
        self.client.app.state.temporal = self.pipeline  # type: ignore[attr-defined]

    def approve(self, sha: str, version: int = 1, token: str | None = None) -> Any:
        return self.call(
            "POST",
            f"/projects/{self.project}/sow/versions/{version}/approve",
            token or self.fde(),
            json={"sha256": sha},
        )

    def sha(self, version: int = 1) -> str:
        return str(self.get(f"/versions/{version}").json()["sha256"])

    def row(self, version: int = 1) -> SowVersion:
        with self.sessions() as s:
            found = s.scalars(select(SowVersion).where(SowVersion.version == version)).one()
            s.expunge_all()
            return found

    def approvals(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "SOW_APPROVED")))

    def outbox(self) -> list[str]:
        with self.sessions() as s:
            return [r.type for r in s.scalars(select(NotificationOutbox))]

    def edited_v2(self) -> None:
        doc = copy.deepcopy(self.structured)
        doc["scope"]["out_of_scope"] = ["Invoicing"]
        from sbam_api.sow.tiptap import to_tiptap

        assert self.save(to_tiptap(doc)).status_code == 201


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e7_1_t01_approving_the_latest_version_with_its_hash(h: Harness) -> None:
    sha = h.sha()
    res = h.approve(sha)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["version"], body["status"], body["is_locked"]) == (1, "APPROVED", True)
    assert body["sha256"] == sha and body["approved_by"] and body["approved_at"]
    row = h.row()
    assert row.state == "APPROVED" and row.approved_by == body["approved_by"]
    with h.sessions() as s:
        assert s.scalars(select(Sow.approved_version_id)).one() == row.id
    [audit] = h.approvals()
    assert audit.after is not None and audit.after["sha256"] == sha and audit.after["version"] == 1
    assert "sow_version.approve" in h.outbox()  # the in-app notification
    [(name, payload)] = h.pipeline.signals
    assert name == "sow_approved"
    assert (payload.sow_version_id, payload.sha256) == (row.id, sha)


def test_e7_1_t03_a_stale_hash_is_409_and_nothing_happens(h: Harness) -> None:
    v1 = h.sha()
    h.edited_v2()
    problem(h.approve(v1, version=2), 409, "SOW_STALE")
    assert h.row(2).state == "IN_REVIEW"
    assert h.approvals() == [] and h.pipeline.signals == []


def test_e7_1_tampered_content_is_stale(h: Harness) -> None:
    sha = h.sha()
    with h.sessions() as s:  # SQLite has no trigger: what Postgres's 0020 trigger would refuse
        s.execute(update(SowVersion).values(structured={"tampered": True}))
        s.commit()
    problem(h.approve(sha), 409, "SOW_STALE")


def test_e7_1_t04_an_older_version_is_not_approved(h: Harness) -> None:
    v1 = h.sha()
    h.edited_v2()
    problem(h.approve(v1, version=1), 409, "SOW_NOT_LATEST")
    assert h.pipeline.signals == []


def test_e7_1_t05_a_malformed_hash_is_422_on_sha256(h: Harness) -> None:
    body = problem(h.approve("abc"), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["sha256"]
    problem(h.approve("A" * 64), 422, "VALIDATION_ERROR")  # lowercase hex only


def test_e7_1_t06_admin_other_tenant_and_service_accounts_cannot_approve(h: Harness) -> None:
    sha = h.sha()
    admin = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    problem(h.approve(sha, token=admin), 403, "WRONG_PORTAL")
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.approve(sha, token=other), 404, "NOT_FOUND")
    robot = h.realm.token(FDE_A1, preferred_username="service-account-sbam-ci")
    problem(h.approve(sha, token=robot), 403, "WRONG_PORTAL")
    assert h.row().state != "APPROVED" and h.pipeline.signals == []


def test_e7_1_t07_temporal_down_is_503_and_nothing_is_approved(h: Harness) -> None:
    h.pipeline.down = True
    problem(h.approve(h.sha()), 503, "ORCHESTRATOR_UNAVAILABLE")
    assert h.row().state == "GENERATED" and h.approvals() == []


def test_e7_1_a_pipeline_that_is_not_waiting_is_409(h: Harness) -> None:
    h.pipeline.stage = PipelineStage.SOW
    problem(h.approve(h.sha()), 409, "PIPELINE_NOT_WAITING")
    assert h.row().state == "GENERATED" and h.approvals() == []


def test_e7_1_t08_approving_twice_answers_200_once_signalled(h: Harness) -> None:
    sha = h.sha()
    first, second = h.approve(sha), h.approve(sha)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(h.approvals()) == 1 and len(h.pipeline.signals) == 1


def test_e7_1_t09_saving_after_approval_is_423(h: Harness) -> None:
    assert h.approve(h.sha()).status_code == 200
    problem(h.save(h.edited()), 423, "SOW_LOCKED")


def test_e7_1_a_failed_generation_cannot_be_approved(h: Harness) -> None:
    with h.sessions() as s:
        s.execute(update(SowVersion).values(state="GENERATION_FAILED"))
        s.commit()
    problem(h.approve(h.sha()), 409, "INVALID_TRANSITION")


def test_e7_1_a_newer_approval_supersedes_the_previous_one(h: Harness) -> None:
    assert h.approve(h.sha()).status_code == 200
    h.generated(h.structured)  # v2, as a scope change or regeneration writes on top of v1
    assert h.approve(h.sha(2), version=2).status_code == 200
    assert (h.row(1).state, h.row(2).state) == ("SUPERSEDED", "APPROVED")
    with h.sessions() as s:
        assert s.scalars(select(Sow.approved_version_id)).one() == h.row(2).id
    assert len(h.pipeline.signals) == 2
