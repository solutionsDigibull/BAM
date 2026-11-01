"""E13.2 `sandbox_runs` repository on SQLite: the claim rules that do not need Postgres.

The advisory lock, the frozen trigger and the migration are covered against Postgres in
`tests/integration/test_e13_2_sandbox_runs_db.py`.
"""

import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sbam_api.builds.models import CodeAttempt
from sbam_api.db.base import Base
from sbam_api.db.repository import TenantContext, TenantContextMissing
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sandbox.repository import Claim, RunOutcome, SandboxRunRepository
from sbam_contracts.sandbox import FailureCategory, InfraReason, SandboxMode, SandboxRunStatus
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

TENANT, OTHER, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    fd, path = tempfile.mkstemp(prefix="sbam-sbx-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", execution_options={"schema_translate_map": {"sbam": None}}
    )
    tables = [CodeAttempt.__table__, SandboxRunRecord.__table__]
    Base.metadata.create_all(engine, tables=[t for t in tables if isinstance(t, Table)])
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()
    os.unlink(path)


def repo(session: Session, tenant: uuid.UUID = TENANT) -> SandboxRunRepository:
    return SandboxRunRepository(session, TenantContext(tenant_id=tenant, project_id=PROJECT))


def attempt(session: Session, tenant: uuid.UUID = TENANT) -> uuid.UUID:
    row = CodeAttempt(
        tenant_id=tenant,
        project_id=PROJECT,
        workflow_id=uuid.uuid4(),
        sub_skill_id=uuid.uuid4(),
        build_no=1,
        cycle=0,
        attempt=0,
        template_name="python-tool",
        template_version="1.0.0",
        template_sha256="a" * 64,
        status="ASSEMBLED",
        bodies={},
        trace_id="t",
    )
    session.add(row)
    session.flush()
    return row.id


def claim(
    r: SandboxRunRepository,
    aid: uuid.UUID,
    dataset: uuid.UUID,
    *,
    activity: str = "act",
    n: int = 1,
    digest: str = DIGEST,
    mode: SandboxMode = SandboxMode.CUBESANDBOX,
    when: datetime = NOW,
) -> Claim:
    return r.claim(
        code_attempt_id=aid,
        dataset_id=dataset,
        mode=mode,
        runtime_digest=digest,
        workflow_id="build-x",
        activity_id=activity,
        activity_attempt=n,
        now=when,
    )


def test_e13_2_a_repository_without_a_tenant_refuses_to_exist(session: Session) -> None:
    with pytest.raises(TenantContextMissing):
        SandboxRunRepository(session, None)


def test_e13_2_the_first_claim_is_run_one_started_and_stamped_with_the_mode(
    session: Session,
) -> None:
    c = claim(repo(session), attempt(session), uuid.uuid4(), mode=SandboxMode.LOCAL_DOCKER_NONCERT)
    assert c.replay is False and c.row.run_no == 1
    assert c.row.status == SandboxRunStatus.STARTED and c.row.is_frozen is False
    assert c.row.mode == "LOCAL_DOCKER_NONCERT"  # the row carries the mode that decides certifying


def test_e13_2_either_digest_spelling_is_stored_in_the_one_prefixed_form(session: Session) -> None:
    c = claim(repo(session), attempt(session), uuid.uuid4(), digest="a" * 64)
    assert c.row.runtime_digest == DIGEST


@pytest.mark.parametrize(
    "digest", ["", "latest", "sha256:" + "A" * 64, "sha256:" + "a" * 64 + "\n"]
)
def test_e13_2_a_malformed_digest_cannot_be_claimed(session: Session, digest: str) -> None:
    with pytest.raises(ValueError):
        claim(repo(session), attempt(session), uuid.uuid4(), digest=digest)


def test_e13_2_t07_a_retry_closes_the_stalled_run_as_superseded_and_takes_the_next_number(
    session: Session,
) -> None:
    r, aid, dataset = repo(session), attempt(session), uuid.uuid4()
    first = claim(r, aid, dataset, n=1).row
    r.record_vm(first, "vm-1")
    second = claim(r, aid, dataset, n=2, when=NOW + timedelta(minutes=5)).row
    assert second.run_no == 2 and second.status == SandboxRunStatus.STARTED
    assert first.status == SandboxRunStatus.FINISHED and first.is_frozen is True
    assert first.failure_category == FailureCategory.INFRA_ERROR
    assert first.infra_reason == InfraReason.SUPERSEDED
    assert first.vm_id == "vm-1"  # kept, so the reaper can destroy it
    assert [x.status for x in r.of_attempt(aid, dataset)].count("STARTED") == 1


def test_e13_2_a_finished_test_outcome_of_the_same_activity_replays_without_a_new_row(
    session: Session,
) -> None:
    r, aid, dataset = repo(session), attempt(session), uuid.uuid4()
    done = claim(r, aid, dataset, activity="a1").row
    r.finish(done, RunOutcome(failure_category=FailureCategory.OOM, exit_code=137), NOW)
    again = claim(r, aid, dataset, activity="a1", n=2)
    assert again.replay is True and again.row.id == done.id
    assert len(r.of_attempt(aid, dataset)) == 1


def test_e13_2_a_clean_pass_replays_too(session: Session) -> None:
    r, aid, dataset = repo(session), attempt(session), uuid.uuid4()
    done = claim(r, aid, dataset, activity="a1").row
    r.finish(done, RunOutcome(exit_code=0), NOW)
    assert claim(r, aid, dataset, activity="a1", n=2).replay is True


def test_e13_2_an_infra_failure_is_rerun_not_replayed(session: Session) -> None:
    r, aid, dataset = repo(session), attempt(session), uuid.uuid4()
    failed = claim(r, aid, dataset, activity="a1").row
    r.finish(
        failed,
        RunOutcome(
            failure_category=FailureCategory.INFRA_ERROR, infra_reason=InfraReason.HOST_UNREACHABLE
        ),
        NOW,
    )
    retry = claim(r, aid, dataset, activity="a1", n=2)
    assert retry.replay is False and retry.row.run_no == 2


def test_e13_2_a_finished_run_of_another_activity_is_history_not_an_answer(
    session: Session,
) -> None:
    r, aid, dataset = repo(session), attempt(session), uuid.uuid4()
    old = claim(r, aid, dataset, activity="old").row
    r.finish(old, RunOutcome(exit_code=0), NOW)
    fresh = claim(r, aid, dataset, activity="new")
    assert fresh.replay is False and fresh.row.run_no == 2


def test_e13_2_numbers_are_per_dataset(session: Session) -> None:
    r, aid = repo(session), attempt(session)
    a, b = claim(r, aid, uuid.uuid4()).row, claim(r, aid, uuid.uuid4()).row
    assert (a.run_no, b.run_no) == (1, 1)


def test_e13_2_another_tenant_sees_nothing_and_has_its_own_numbers(session: Session) -> None:
    mine = claim(repo(session), attempt(session), (dataset := uuid.uuid4())).row
    assert repo(session, OTHER).get(mine.id) is None
    theirs = claim(repo(session, OTHER), attempt(session, OTHER), dataset).row
    assert theirs.run_no == 1 and theirs.tenant_id == OTHER


def test_e13_2_finish_freezes_stamps_the_time_and_computes_the_duration(session: Session) -> None:
    r = repo(session)
    row = claim(r, attempt(session), uuid.uuid4()).row
    r.finish(
        row,
        RunOutcome(exit_code=0, truncated=True, summary={"cases": 3}),
        NOW + timedelta(seconds=7),
    )
    assert row.status == SandboxRunStatus.FINISHED and row.is_frozen is True
    assert row.duration_ms == 7000 and row.truncated is True and row.summary == {"cases": 3}
    assert row.finished_at == NOW + timedelta(seconds=7)


def test_e13_2_a_negative_clock_skew_gives_zero_not_a_negative_duration(session: Session) -> None:
    r = repo(session)
    row = claim(r, attempt(session), uuid.uuid4()).row
    r.finish(row, RunOutcome(exit_code=0), NOW - timedelta(seconds=30))
    assert row.duration_ms == 0


def test_e13_2_an_outcome_cannot_name_an_infra_reason_without_infra_error(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    r = repo(session)
    row = claim(r, attempt(session), uuid.uuid4()).row
    with pytest.raises(IntegrityError):
        r.finish(row, RunOutcome(infra_reason=InfraReason.HASH_MISMATCH, exit_code=1), NOW)
