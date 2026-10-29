"""E16.2: one Langfuse project per environment, checked before a worker polls (TD §17.2)."""

import logging
from typing import Any

import httpx
import pytest
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.llm.preflight import MISCONFIGURED, check_langfuse, project_name
from sbam_workers.registry import PreflightError
from sbam_workers.settings import WorkerSettings

KEYS = {
    "langfuse_host": "http://langfuse-web:3000",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_secret_key": "sk-lf-test",
}


def conf(env: str, **overrides: Any) -> WorkerSettings:
    values: dict[str, Any] = {"temporal_host": "temporal:7233", "database_url": "postgresql://x"}
    return WorkerSettings(**{**values, "sbam_env": env, **overrides})


def answer(status: int = 200, body: Any = None) -> Any:
    def fetch(_: WorkerSettings) -> httpx.Response:
        request = httpx.Request("GET", "http://langfuse-web:3000/api/public/projects")
        return httpx.Response(status, json=body, request=request)

    return fetch


def project(name: str) -> Any:
    return answer(body={"data": [{"id": "p1", "name": name}]})


def unreachable(_: WorkerSettings) -> httpx.Response:
    raise httpx.ConnectError("refused")


def refused(s: WorkerSettings, fetch: Any = unreachable) -> str:
    with pytest.raises(PreflightError) as exc:
        check_langfuse(s, fetch)
    assert exc.value.code == MISCONFIGURED
    return str(exc.value)


def test_e16_2_project_names() -> None:
    assert [project_name(e) for e in ("development", "staging", "production", "prod")] == [
        "sbam-dev",
        "sbam-staging",
        "sbam-prod",
        "sbam-prod",
    ]


def test_e16_2_t04_staging_without_keys_fails_fast() -> None:
    message = refused(conf("staging"))
    assert "LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY required" in message
    refused(conf("staging", **{**KEYS, "langfuse_secret_key": ""}))  # Compose passes "" if unset


def test_e16_2_t04_keys_of_another_project_refused() -> None:
    message = refused(conf("staging", **KEYS), project("sbam-prod"))
    assert "sbam-staging" in message and "sk-lf-test" not in message


def test_e16_2_t04_keys_of_this_project_pass() -> None:
    check_langfuse(conf("staging", **KEYS), project("sbam-staging"))
    check_langfuse(conf("production", **KEYS), project("sbam-prod"))


def test_e16_2_t04_rejected_keys_refused() -> None:
    assert "rejected" in refused(conf("prod", **KEYS), answer(401, {"message": "no"}))


def test_e16_2_t05_prod_refuses_prompt_content() -> None:
    message = refused(conf("production", **KEYS, langfuse_log_content=True), project("sbam-prod"))
    assert "LANGFUSE_LOG_CONTENT must be false" in message
    check_langfuse(conf("development", **KEYS, langfuse_log_content=True), project("sbam-dev"))


def test_e16_2_public_host_refused_outside_development() -> None:
    public = {**KEYS, "langfuse_host": "https://cloud.langfuse.com"}
    assert "internal address" in refused(conf("staging", **public), project("sbam-staging"))
    check_langfuse(
        conf("staging", **{**KEYS, "langfuse_host": "http://10.0.0.5:3000"}),
        project("sbam-staging"),
    )


def test_e16_2_t06_langfuse_down_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, "sbam_workers.llm.preflight")
    check_langfuse(conf("staging", **KEYS), unreachable)
    check_langfuse(conf("staging", **KEYS), answer(503, {}))
    assert caplog.text.count("langfuse project not checked") == 2


def test_e16_2_development_is_lenient(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, "sbam_workers.llm.preflight")
    check_langfuse(conf("development"), unreachable)  # no keys: no traces, never called
    check_langfuse(conf("development", **KEYS), project("my-laptop"))
    assert "not those of project sbam-dev" in caplog.text


@pytest.mark.parametrize("env", [" production", "prod-eu", "live", "uat", "staging-2"])
def test_a27_t06_unlisted_spelling_is_strict_like_production(env: str) -> None:
    with pytest.raises(PreflightError, match=MISCONFIGURED):  # no keys: strict mode refuses
        check_langfuse(conf(env), unreachable)


def test_e16_2_preflight_runs_on_the_llm_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.load_all()
    for queue in (TaskQueue.INGEST, TaskQueue.LLM, TaskQueue.BUILD, TaskQueue.TEST):
        assert "langfuse_preflight" in [f.__name__ for f in registry._PREFLIGHT[queue]]
    monkeypatch.setenv("TEMPORAL_HOST", "temporal:7233")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("SBAM_ENV", "staging")
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "")
    with pytest.raises(PreflightError, match=MISCONFIGURED):
        registry.run_preflight([TaskQueue.LLM])
