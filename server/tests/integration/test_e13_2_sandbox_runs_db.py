"""E13.2 `sandbox_runs` against the Compose Postgres: migration 0040 up and down, every row
constraint, the frozen trigger, tenant scoping, `claim` semantics and a race between claimers. A
scratch database is created and dropped. Needs `make up`; not part of the unit run."""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from sbam_api.builds.models import CodeAttempt
from sbam_api.db.migrate import config
from sbam_api.db.repository import RowFrozen, TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.sandbox.models import SandboxRunRecord
from sbam_api.sandbox.repository import RunOutcome, SandboxRunRepository
from sbam_contracts.sandbox import FailureCategory, InfraReason, SandboxMode
from sqlalchemy import pool, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e132_{uuid.uuid4().hex[:10]}"
TENANT, OTHER, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
PARENT = "0039_workflow_manifests"


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
            command.downgrade(config(), PARENT)  # 0040 undoes itself
            with engine.connect() as conn:
                found = conn.execute(text("SELECT to_regclass('sbam.sandbox_runs')")).scalar()
                trigger = conn.execute(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname = 'sandbox_runs_frozen'")
                ).scalar()
            assert found is None and trigger == 0
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def ctx(tenant: uuid.UUID = TENANT) -> TenantContext:
    return TenantContext(tenant_id=tenant, project_id=PROJECT)


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
    repo: SandboxRunRepository,
    attempt_id: uuid.UUID,
    dataset: uuid.UUID,
    *,
    activity: str = "act-1",
    n: int = 1,
    when: datetime = NOW,
) -> Any:
    return repo.claim(
        code_attempt_id=attempt_id,
        dataset_id=dataset,
        mode=SandboxMode.CUBESANDBOX,
        runtime_digest=DIGEST,
        workflow_id="build-x",
        activity_id=activity,
        activity_attempt=n,
        now=when,
    )


def raw_row(attempt_id: uuid.UUID, **over: Any) -> SandboxRunRecord:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "code_attempt_id": attempt_id,
        "dataset_id": uuid.uuid4(),
        "run_no": 1,
        "mode": "CUBESANDBOX",
        "status": "STARTED",
        "runtime_digest": DIGEST,
        "truncated": False,
        "is_frozen": False,
        "temporal_workflow_id": "w",
        "activity_id": "a",
        "activity_attempt": 1,
        "started_at": NOW,
    }
    return SandboxRunRecord(**{**values, **over})


# --- the constraints each reject one wrong row ------------------------------------------------

FINISHED: dict[str, Any] = {"status": "FINISHED", "is_frozen": True, "finished_at": NOW}
BAD_ROWS = {
    "unknown mode": {"mode": "DOCKER"},
    "unknown status": {"status": "RUNNING"},
    "unknown failure category": {**FINISHED, "failure_category": "BOOM"},
    "unknown infra reason": {
        **FINISHED,
        "failure_category": "INFRA_ERROR",
        "infra_reason": "BAD_LUCK",
    },
    "run_no zero": {"run_no": 0},
    "negative duration": {"duration_ms": -1},
    "digest without prefix": {"runtime_digest": "a" * 64},
    "digest with wrong prefix": {"runtime_digest": "sha512:" + "a" * 64},
    "finished but not frozen": {"status": "FINISHED", "is_frozen": False, "finished_at": NOW},
    "started but frozen": {"is_frozen": True},
    "finished without finished_at": {"status": "FINISHED", "is_frozen": True},
    "started with finished_at": {"finished_at": NOW},
    "infra reason without INFRA_ERROR": {**FINISHED, "infra_reason": "HASH_MISMATCH"},
    "INFRA_ERROR without a reason": {**FINISHED, "failure_category": "INFRA_ERROR"},
    "TIMEOUT with an infra reason": {
        **FINISHED,
        "failure_category": "TIMEOUT",
        "infra_reason": "HASH_MISMATCH",
    },
    "outcome on a started run": {"failure_category": "TIMEOUT"},
    "exit code on a started run": {"exit_code": 0},
}


@pytest.mark.parametrize("name", list(BAD_ROWS))
def test_e13_2_the_database_refuses(sessions: sessionmaker[Session], name: str) -> None:
    with sessions() as session:
        aid = attempt(session)
        session.add(raw_row(aid, **BAD_ROWS[name]))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_e13_2_the_database_accepts_the_two_legal_shapes(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        aid = attempt(session)
        session.add(raw_row(aid))
        session.add(
            raw_row(
                aid,
                run_no=2,
                **FINISHED,
                failure_category="INFRA_ERROR",
                infra_reason="HOST_UNREACHABLE",
            )
        )
        session.add(raw_row(aid, run_no=3, **FINISHED, exit_code=0))
        session.flush()
        session.rollback()


def test_e13_2_a_code_attempt_must_exist(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        session.add(raw_row(uuid.uuid4()))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# --- finished evidence is immutable -----------------------------------------------------------


def test_e13_2_a_finished_run_cannot_be_changed_or_deleted(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid = attempt(session)
        row = claim(repo, aid, uuid.uuid4()).row
        repo.finish(row, RunOutcome(exit_code=0, vm_id="vm-1"), NOW)
        session.commit()
        run_id = row.id
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        again = repo.get(run_id)
        assert again is not None and again.is_frozen and again.status == "FINISHED"
        with pytest.raises(RowFrozen):
            repo.finish(again, RunOutcome(failure_category=FailureCategory.TIMEOUT), NOW)
        session.rollback()
    with sessions() as session:
        for sql in (
            "UPDATE sbam.sandbox_runs SET exit_code = 1 WHERE id = :i",
            "UPDATE sbam.sandbox_runs SET is_frozen = false, status = 'STARTED',"
            " finished_at = NULL WHERE id = :i",  # try to un-finish it
            "UPDATE sbam.sandbox_runs SET failure_category = NULL WHERE id = :i",
            "DELETE FROM sbam.sandbox_runs WHERE id = :i",
        ):
            with pytest.raises(DBAPIError, match="frozen"):
                session.execute(text(sql), {"i": run_id})
            session.rollback()


# --- tenant scoping ---------------------------------------------------------------------------


def test_e13_2_another_tenant_cannot_see_a_run_and_may_reuse_the_key(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        aid, dataset = attempt(session), uuid.uuid4()
        mine = claim(SandboxRunRepository(session, ctx()), aid, dataset).row
        assert SandboxRunRepository(session, ctx(OTHER)).get(mine.id) is None
        assert SandboxRunRepository(session, ctx(OTHER)).of_attempt(aid) == []
        # the other tenant's own attempt may use the same (dataset, run_no) numbers
        other_aid = attempt(session, OTHER)
        other = claim(SandboxRunRepository(session, ctx(OTHER)), other_aid, dataset).row
        assert other.run_no == mine.run_no == 1
        session.rollback()


# --- claim ------------------------------------------------------------------------------------


def test_e13_2_a_new_claim_is_run_one_and_started(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        c = claim(repo, attempt(session), uuid.uuid4())
        assert (c.replay, c.row.run_no, c.row.status, c.row.is_frozen) == (
            False,
            1,
            "STARTED",
            False,
        )
        assert c.row.runtime_digest == DIGEST and c.row.mode == "CUBESANDBOX"
        session.rollback()


def test_e13_2_t07_a_retry_supersedes_the_stalled_run_and_the_stalled_run_cannot_finish(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid, dataset = attempt(session), uuid.uuid4()
        first = claim(repo, aid, dataset, n=1).row
        repo.record_vm(first, "vm-first")
        second = claim(repo, aid, dataset, n=2, when=NOW.replace(minute=5)).row  # the retry
        assert second.run_no == 2 and second.status == "STARTED"
        session.flush()
        assert (first.status, first.failure_category, first.infra_reason, first.is_frozen) == (
            "FINISHED",
            "INFRA_ERROR",
            InfraReason.SUPERSEDED.value,
            True,
        )
        assert first.vm_id == "vm-first"  # the reaper still knows which VM to destroy
        session.commit()
        first_id = first.id
    # the stalled worker wakes up and reports: it meets the frozen row, nothing is overwritten
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        stale = repo.get(first_id)
        assert stale is not None
        with pytest.raises(RowFrozen):
            repo.finish(stale, RunOutcome(exit_code=0), NOW)
        session.rollback()


def test_e13_2_an_activity_that_already_finished_with_a_test_outcome_replays(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid, dataset = attempt(session), uuid.uuid4()
        done = claim(repo, aid, dataset, activity="act-9").row
        repo.finish(done, RunOutcome(failure_category=FailureCategory.TIMEOUT, exit_code=1), NOW)
        replay = claim(repo, aid, dataset, activity="act-9", n=2)
        assert replay.replay is True and replay.row.id == done.id
        assert len(repo.of_attempt(aid, dataset)) == 1  # no second row
        session.rollback()


def test_e13_2_an_infra_failure_is_never_replayed_it_is_rerun(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid, dataset = attempt(session), uuid.uuid4()
        failed = claim(repo, aid, dataset, activity="act-3").row
        repo.finish(
            failed,
            RunOutcome(
                failure_category=FailureCategory.INFRA_ERROR,
                infra_reason=InfraReason.HOST_UNREACHABLE,
            ),
            NOW,
        )
        retry = claim(repo, aid, dataset, activity="act-3", n=2)
        assert retry.replay is False and retry.row.run_no == 2
        session.rollback()


def test_e13_2_a_clean_pass_of_another_activity_does_not_replay_for_this_one(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid, dataset = attempt(session), uuid.uuid4()
        old = claim(repo, aid, dataset, activity="act-old").row
        repo.finish(old, RunOutcome(exit_code=0), NOW)
        fresh = claim(repo, aid, dataset, activity="act-new")
        assert fresh.replay is False and fresh.row.run_no == 2  # a re-run is new evidence
        session.rollback()


def test_e13_2_finish_computes_duration_when_the_caller_gives_none(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        row = claim(repo, attempt(session), uuid.uuid4()).row
        repo.finish(row, RunOutcome(exit_code=0), NOW.replace(second=7))
        assert row.duration_ms == 7000
        session.rollback()


def test_e13_2_an_empty_digest_or_a_forged_one_cannot_be_claimed(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = SandboxRunRepository(session, ctx())
        aid = attempt(session)
        for bad in ("", "latest", "sha256:" + "A" * 64, "sha256:" + "a" * 64 + "\n"):
            with pytest.raises(ValueError):
                repo.claim(
                    code_attempt_id=aid,
                    dataset_id=uuid.uuid4(),
                    mode=SandboxMode.CUBESANDBOX,
                    runtime_digest=bad,
                    workflow_id="w",
                    activity_id="a",
                    activity_attempt=1,
                    now=NOW,
                )
        session.rollback()


# --- the race ---------------------------------------------------------------------------------


def test_e13_2_t07_racing_claimers_leave_at_most_one_live_run_and_unique_numbers(
    sessions: sessionmaker[Session],
) -> None:
    """Sixteen activity attempts claim the same (code attempt, dataset) at once. Whatever the order,
    run numbers are unique and contiguous, and no more than one run is STARTED when they are done.
    """
    with sessions() as session:
        aid = attempt(session)
        session.commit()
    dataset = uuid.uuid4()

    def one(i: int) -> int | str:
        with sessions() as session:
            try:
                row = claim(
                    SandboxRunRepository(session, ctx()), aid, dataset, activity=f"a{i}", n=i
                ).row
                session.commit()
                return int(row.run_no)
            except Exception as exc:
                session.rollback()
                return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=16) as pool_:
        results = list(pool_.map(one, range(16)))
    errors = [r for r in results if isinstance(r, str)]
    assert errors == [], errors
    with sessions() as session:
        rows = SandboxRunRepository(session, ctx()).of_attempt(aid, dataset)
        numbers = [r.run_no for r in rows]
        assert numbers == list(range(1, len(rows) + 1)), numbers
        live = [r for r in rows if r.status == "STARTED"]
        assert len(live) <= 1, [(r.run_no, r.activity_id) for r in live]
        assert len(rows) == 16 and len(set(results)) == 16
