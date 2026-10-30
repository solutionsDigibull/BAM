"""A-03 (#439): the approval commits before the pipeline is told; a failure in between leaves it
saved and findable, and it is sent again by the caller or by the relay. The fake Temporal client of
the approve tests records every signal and every commit in one ordered log."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.sow import approve
from sbam_api.sow.approve import RELAY_MIN_AGE, redeliver_pending
from sbam_api.sow.models import SowVersion
from sbam_contracts.pipeline import PipelineStage
from sqlalchemy import event, select, update
from temporalio.service import RPCError, RPCStatusCode
from test_auth import TENANT_A, problem
from test_sow_approve_api import Harness as ApproveHarness


class Harness(ApproveHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.log: list[str] = []
        event.listen(self.sessions.kw["bind"], "commit", lambda _c: self.log.append("commit"))

        def written(_m: Any, _c: Any, row: AuditLog) -> None:
            if row.action == "SOW_APPROVED":
                self.log.append("approved")

        event.listen(AuditLog, "after_insert", written)
        self.unlisten: Any = lambda: event.remove(AuditLog, "after_insert", written)
        handle_signal = type(self.pipeline.get_workflow_handle("x")).signal
        harness = self

        async def signal(handle: Any, name: str, payload: Any, **kw: Any) -> None:
            if harness.fail_signal:
                raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
            harness.log.append("signal")
            await handle_signal(handle, name, payload, **kw)

        type(self.pipeline.get_workflow_handle("x")).signal = signal  # type: ignore[method-assign,assignment]
        self.fail_signal = False
        self.restore: Any = handle_signal

    def sent_at(self, version: int = 1) -> Any:
        return self.row(version).signal_sent_at

    def ctx(self) -> TenantContext:
        return TenantContext(TENANT_A, uuid.UUID(self.project))

    async def get_pipeline(self) -> Any:
        return self.pipeline

    def relay(self) -> tuple[int, int]:
        return asyncio.run(
            redeliver_pending(self.sessions, self.get_pipeline, utcnow() + RELAY_MIN_AGE)
        )

    def age(self, minutes: int = 5) -> None:
        with self.sessions() as s:
            when = utcnow() - timedelta(minutes=minutes)
            s.execute(update(SowVersion).values(approved_at=when))
            s.commit()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path)
    yield harness
    type(harness.pipeline.get_workflow_handle("x")).signal = harness.restore  # type: ignore[method-assign]
    harness.unlisten()


def test_a03_t01_the_approval_commits_before_the_signal_is_sent(h: Harness) -> None:
    """approved row written, then committed, then (and only then) the signal. Telling Temporal
    before the commit is the race: `create_workflow` would read an unapproved version."""
    assert h.approve(h.sha()).status_code == 200
    order = [e for e in h.log if e in ("approved", "signal")]
    assert order == ["approved", "signal"]
    written, signal = h.log.index("approved"), h.log.index("signal")
    assert "commit" in h.log[written:signal]


def test_a03_t02_delivery_is_recorded(h: Harness) -> None:
    assert h.sent_at() is None
    assert h.approve(h.sha()).status_code == 200
    assert h.sent_at() is not None and len(h.pipeline.signals) == 1


def test_a03_t03_a_signal_that_fails_after_the_commit_leaves_it_saved_and_pending(
    h: Harness,
) -> None:
    h.fail_signal = True
    problem(h.approve(h.sha()), 503, "ORCHESTRATOR_UNAVAILABLE")
    assert h.row().state == "APPROVED" and h.sent_at() is None  # saved, not yet sent
    assert len(h.approvals()) == 1 and h.pipeline.signals == []


def test_a03_t04_approving_again_sends_it_once_and_answers_200(h: Harness) -> None:
    sha = h.sha()
    h.fail_signal = True
    assert h.approve(sha).status_code == 503
    h.fail_signal = False
    again = h.approve(sha)
    assert again.status_code == 200 and again.json()["status"] == "APPROVED"
    assert len(h.pipeline.signals) == 1 and h.sent_at() is not None
    assert len(h.approvals()) == 1  # one audit row: the retry is not a second approval
    assert (
        h.approve(sha).status_code == 200 and len(h.pipeline.signals) == 1
    )  # and not a third send


def test_a03_t05_the_pipeline_moving_on_between_check_and_signal_is_409_and_resendable(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = h.sha()
    real = approve._precheck

    async def late(*a: Any, **k: Any) -> None:
        await real(*a, **k)
        h.pipeline.stage = PipelineStage.SOW  # the pipeline left the approval step meanwhile

    monkeypatch.setattr(approve, "_precheck", late)
    problem(h.approve(sha), 409, "PIPELINE_NOT_WAITING")
    assert h.row().state == "APPROVED" and h.sent_at() is None
    h.pipeline.stage = PipelineStage.WAIT_SOW_APPROVAL
    assert h.approve(sha).status_code == 200 and len(h.pipeline.signals) == 1


def test_a03_t06_a_busy_pipeline_stops_a_scope_change_before_anything_is_approved(
    h: Harness,
) -> None:
    assert h.approve(h.sha()).status_code == 200
    h.generated(h.structured)  # v2
    h.pipeline.stage = PipelineStage.EXTRACT_MACROS
    problem(h.approve(h.sha(2), version=2), 409, "PIPELINE_RUNNING")
    assert h.row(2).state != "APPROVED" and h.row(1).state == "APPROVED"
    assert h.pipeline.started == []


def test_a03_t07_the_relay_sends_what_nobody_came_back_for(h: Harness) -> None:
    h.fail_signal = True
    assert h.approve(h.sha()).status_code == 503
    h.fail_signal = False
    h.age()
    assert h.relay() == (1, 0)
    assert len(h.pipeline.signals) == 1 and h.sent_at() is not None
    assert h.relay() == (0, 0) and len(h.pipeline.signals) == 1  # nothing left, nothing resent


def test_a03_t08_the_relay_leaves_young_approvals_to_their_own_request(h: Harness) -> None:
    h.fail_signal = True
    h.approve(h.sha())
    h.fail_signal = False
    assert asyncio.run(redeliver_pending(h.sessions, h.get_pipeline, utcnow())) == (0, 0)
    assert h.pipeline.signals == [] and h.sent_at() is None


def test_a03_t09_the_relay_counts_a_failure_and_tries_again_next_run(h: Harness) -> None:
    h.fail_signal = True
    h.approve(h.sha())
    h.age()
    assert h.relay() == (0, 1) and h.sent_at() is None
    h.fail_signal = False
    assert h.relay() == (1, 0) and h.sent_at() is not None


def test_a03_t10_a_relayed_scope_change_replaces_the_run_instead_of_signalling(
    h: Harness,
) -> None:
    assert h.approve(h.sha()).status_code == 200
    h.generated(h.structured)
    h.pipeline.down = True
    h.pipeline.stage = PipelineStage.WAIT_SOW_APPROVAL
    # the pre-check cannot reach Temporal: nothing is approved
    problem(h.approve(h.sha(2), version=2), 503, "ORCHESTRATOR_UNAVAILABLE")
    assert h.row(2).state != "APPROVED"
    h.pipeline.down = False
    # approve with the pre-check passing but the replacement failing: saved, pending, then relayed
    real = h.pipeline.start_workflow

    async def failing_start(*a: Any, **k: Any) -> Any:
        raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

    h.pipeline.start_workflow = failing_start  # type: ignore[method-assign]
    assert h.approve(h.sha(2), version=2).status_code == 503
    assert h.row(2).state == "APPROVED" and h.sent_at(2) is None
    h.pipeline.start_workflow = real  # type: ignore[method-assign]
    h.age()
    sent, failed = h.relay()
    assert (sent, failed) == (1, 0) and len(h.pipeline.started) == 1
    assert h.sent_at(2) is not None


def test_a03_t11_a_pipeline_that_already_took_the_approval_counts_as_delivered(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = h.sha()
    h.fail_signal = True
    assert h.approve(sha).status_code == 503
    h.fail_signal = False
    h.pipeline.stage = PipelineStage.EXTRACT_MACROS  # it took the first signal after all
    monkeypatch.setattr(approve, "_workflow_exists", lambda *_a: True)
    assert h.approve(sha).status_code == 200  # not a 409: this version's Workflow exists
    assert h.sent_at() is not None and h.pipeline.signals == []


def test_a03_t12_without_a_workflow_a_busy_pipeline_stays_pending_and_says_so(h: Harness) -> None:
    sha = h.sha()
    h.fail_signal = True
    h.approve(sha)
    h.fail_signal = False
    h.pipeline.stage = PipelineStage.SOW  # not at the approval step, and no Workflow exists
    problem(h.approve(sha), 409, "PIPELINE_NOT_WAITING")
    assert h.sent_at() is None  # still pending: the signal was not lost


def test_a03_t13_only_approved_unsent_versions_are_relayed(h: Harness) -> None:
    sha = h.sha()
    assert h.approve(sha).status_code == 200  # delivered
    h.age()
    with h.sessions() as s:
        assert s.scalars(select(SowVersion.signal_sent_at)).one() is not None
    assert h.relay() == (0, 0) and len(h.pipeline.signals) == 1
