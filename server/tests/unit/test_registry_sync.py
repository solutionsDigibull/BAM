"""E12.1 registry sync: one transaction, retries, idempotent per digest, one active registry."""

from pathlib import Path
from typing import Any

import pytest
from registry_db import sample_registry, sqlite_sessions
from sbam_api.registry.models import RuntimeRegistryPackageRow
from sbam_api.registry.repository import (
    RegistryConflictError,
    SyncOutcome,
    active_registry,
    registry_digests,
)
from sbam_api.registry.sync import main, sync
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return sqlite_sessions()


def package_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        return session.query(RuntimeRegistryPackageRow).count()


def test_e12_1_sync_stores_registry_and_activates(sessions: sessionmaker[Session]) -> None:
    registry = sample_registry()
    assert sync(sessions, registry, activate=True) is SyncOutcome.CREATED
    with sessions() as session:
        stored = active_registry(session)
    assert stored == registry  # same packages, same hash, round trip through the tables
    assert package_count(sessions) == len(registry.packages)


def test_e12_1_same_digest_twice_is_a_no_op(sessions: sessionmaker[Session]) -> None:
    registry = sample_registry()
    sync(sessions, registry, activate=True)
    assert sync(sessions, registry, activate=True) is SyncOutcome.UNCHANGED
    assert package_count(sessions) == len(registry.packages)


def test_e12_1_same_digest_other_content_is_refused(sessions: sessionmaker[Session]) -> None:
    sync(sessions, sample_registry(), activate=True)
    with pytest.raises(RegistryConflictError):
        sync(sessions, sample_registry(python_version="3.12.0"), activate=True)


def test_e12_1_one_active_registry_and_older_ones_kept(sessions: sessionmaker[Session]) -> None:
    sync(sessions, sample_registry("aa"), activate=True)
    sync(sessions, sample_registry("bb"), activate=False)  # stored, not active yet
    with sessions() as session:
        assert registry_digests(session) == [
            ("sha256:" + "aa" * 32, True),
            ("sha256:" + "bb" * 32, False),
        ]
    sync(sessions, sample_registry("bb"), activate=True)
    with sessions() as session:
        assert registry_digests(session) == [
            ("sha256:" + "aa" * 32, False),
            ("sha256:" + "bb" * 32, True),
        ]
        active = active_registry(session)
    assert active is not None and active.image_digest == "sha256:" + "bb" * 32


class FlakySessions:
    """Fails with OperationalError (DB down) on the first `failures` transactions."""

    def __init__(self, real: sessionmaker[Session], failures: int) -> None:
        self.real, self.failures, self.calls = real, failures, 0

    def __call__(self) -> Session:
        self.calls += 1
        if self.calls <= self.failures:
            raise OperationalError("connect", {}, ConnectionError("db down"))
        return self.real()


def test_e12_1_t06_db_down_is_retried(sessions: sessionmaker[Session]) -> None:
    flaky = FlakySessions(sessions, failures=2)
    waits: list[float] = []
    outcome = sync(flaky, sample_registry(), activate=True, sleep=waits.append)  # type: ignore[arg-type]
    assert outcome is SyncOutcome.CREATED and flaky.calls == 3 and waits == [1.0, 2.0]


def test_e12_1_t06_db_down_for_good_writes_nothing(sessions: sessionmaker[Session]) -> None:
    flaky = FlakySessions(sessions, failures=99)
    with pytest.raises(OperationalError):
        sync(flaky, sample_registry(), activate=True, sleep=lambda _: None)  # type: ignore[arg-type]
    with sessions() as session:
        assert registry_digests(session) == []
    assert package_count(sessions) == 0


def test_e12_1_t06_failure_mid_transaction_rolls_back(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DB dies after the registry row is written but before the packages: nothing stays."""
    original = Session.add_all

    def dying_add_all(self: Session, rows: Any) -> None:
        original(self, rows)
        raise OperationalError("insert", {}, ConnectionError("connection lost"))

    monkeypatch.setattr(Session, "add_all", dying_add_all)
    with pytest.raises(OperationalError):
        sync(sessions, sample_registry(), activate=True, attempts=2, sleep=lambda _: None)
    monkeypatch.undo()
    with sessions() as session:
        assert registry_digests(session) == []


def test_e12_1_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "registry.json"
    good.write_text(sample_registry().model_dump_json(), encoding="utf-8")
    no_digest = tmp_path / "no_digest.json"
    no_digest.write_text(sample_registry(image_digest=None).model_dump_json(), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")

    db = f"sqlite:///{tmp_path / 'missing-dir' / 'x.db'}"  # unopenable: OperationalError
    assert main([str(good), "--database-url", db, "--attempts", "1"]) == 1
    assert "nothing was written" in capsys.readouterr().err
    assert main([str(no_digest), "--database-url", db]) == 2
    assert main([str(broken), "--database-url", db]) == 2
    assert main([str(good), "--database-url", ""]) == 2
