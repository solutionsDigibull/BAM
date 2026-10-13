"""E1.7 worker bootstrap: `--queues` parsing, exit codes and the registry."""

import pytest
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.bootstrap import build_parser, main
from sbam_workers.queues import QueueError, parse_queues


def test_e1_7_queues_parse_one_or_many() -> None:
    assert parse_queues("sbam-ingest") == [TaskQueue.INGEST]
    assert parse_queues(" sbam-llm, sbam-build ,sbam-llm") == [TaskQueue.LLM, TaskQueue.BUILD]


@pytest.mark.parametrize("value", ["sbam-foo", "sbam-ingest,sbam-foo", "", " , ", None])
def test_e1_7_t02_unknown_or_empty_queue_rejected(value: str | None) -> None:
    with pytest.raises(QueueError) as err:
        parse_queues(value)
    assert "valid queues: sbam-ingest, sbam-knowledge, sbam-llm" in str(err.value)


def test_e1_7_t02_unknown_queue_exits_2_listing_valid_queues(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--queues", "sbam-foo"]) == 2
    err = capsys.readouterr().err
    assert "unknown task queue(s) sbam-foo" in err
    assert all(q.value in err for q in TaskQueue)


def test_e1_7_t02_empty_queues_exit_2(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SBAM_QUEUES", raising=False)
    assert main([]) == 2
    assert "no task queue given" in capsys.readouterr().err


def test_e1_7_queues_default_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBAM_QUEUES", "sbam-test,sbam-certify")
    assert parse_queues(build_parser().parse_args([]).queues) == [TaskQueue.TEST, TaskQueue.CERTIFY]


def test_e1_7_missing_settings_exit_2(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TEMPORAL_HOST", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir("/")  # no .env here
    assert main(["--queues", "sbam-ingest"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_e1_7_every_queue_serves_ping_and_mirror() -> None:
    registry.load_all()
    for queue in TaskQueue:
        workflows, activities = registry.collect(queue)
        assert [w.__name__ for w in workflows] == ["PingWorkflow"]
        names = sorted(getattr(a, "__temporal_activity_definition").name for a in activities)
        assert names == ["ping", "update_run_mirror"]


def test_e1_7_registry_needs_a_queue() -> None:
    with pytest.raises(ValueError, match="at least one"):
        registry.activity_for()
