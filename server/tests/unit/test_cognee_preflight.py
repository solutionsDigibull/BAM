"""E5.1-T04 knowledge worker self-check: access control on, Bifrost only, stores on the volume."""

import pytest
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.bootstrap import main
from sbam_workers.knowledge.preflight import check_cognee_env
from sbam_workers.registry import PreflightError

GOOD = {
    "ENABLE_BACKEND_ACCESS_CONTROL": "true",
    "LLM_ENDPOINT": "http://bifrost:8080/v1",
    "EMBEDDING_ENDPOINT": "http://bifrost:8080/v1",
    "LLM_API_KEY": "sbam-local",
    "EMBEDDING_API_KEY": "sbam-local",
    "LLM_MODEL": "sbam-knowledge",
    "EMBEDDING_MODEL": "sbam-embed",
    "DATA_ROOT_DIRECTORY": "/data/cognee/data",
    "SYSTEM_ROOT_DIRECTORY": "/data/cognee/system",
    "COGNEE_USER_SECRET": "x" * 32,
}


def failure(**changes: str | None) -> PreflightError:
    env = {k: v for k, v in {**GOOD, **changes}.items() if v is not None}
    with pytest.raises(PreflightError) as caught:
        check_cognee_env(env)
    return caught.value


def test_e5_1_good_config_passes() -> None:
    check_cognee_env(GOOD)


@pytest.mark.parametrize("value", ["false", "", None, "0"])
def test_e5_1_t04_access_control_off_fails_with_cognee_acl_disabled(value: str | None) -> None:
    assert failure(ENABLE_BACKEND_ACCESS_CONTROL=value).code == "COGNEE_ACL_DISABLED"


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"LLM_ENDPOINT": "https://openrouter.ai/api/v1"}, "LLM_ENDPOINT"),
        ({"EMBEDDING_ENDPOINT": None}, "EMBEDDING_ENDPOINT"),
        ({"LLM_API_KEY": ""}, "LLM_API_KEY"),
        ({"OPENROUTER_API_KEY": "sk-or-123"}, "OPENROUTER_API_KEY"),
        ({"DATA_ROOT_DIRECTORY": "/var/cognee"}, "DATA_ROOT_DIRECTORY"),
        ({"SYSTEM_ROOT_DIRECTORY": "/data/cognee/../etc"}, "SYSTEM_ROOT_DIRECTORY"),
        ({"COGNEE_USER_SECRET": "short"}, "COGNEE_USER_SECRET"),
    ],
)
def test_e5_1_misconfiguration_names_the_variable_not_its_value(
    changes: dict[str, str | None], named: str
) -> None:
    err = failure(**changes)
    assert err.code == "COGNEE_MISCONFIGURED" and named in str(err)
    assert "sk-or-123" not in str(err)


def test_e5_1_t04_preflight_is_registered_on_the_knowledge_queue_only() -> None:
    registry.load_all()
    assert [f.__name__ for f in registry._PREFLIGHT[TaskQueue.KNOWLEDGE]] == ["cognee_preflight"]
    assert all(not registry._PREFLIGHT[q] for q in TaskQueue if q is not TaskQueue.KNOWLEDGE)


def test_e5_1_t04_worker_with_acl_off_exits_before_polling(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost/x")

    async def never(*_: object, **__: object) -> None:
        raise AssertionError("must not reach Temporal")

    monkeypatch.setattr("sbam_workers.bootstrap.serve", never)
    assert main(["--queues", "sbam-knowledge"]) == 3
    assert "health FAILED COGNEE_ACL_DISABLED" in capsys.readouterr().err


def test_e5_1_other_queues_do_not_need_cognee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    registry.run_preflight([TaskQueue.INGEST, TaskQueue.LLM])  # no error
