"""Preflight: a dead provider key is named and explained before anything is built."""

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl
from sbam_api.core.settings import Settings
from sbam_api.preflight.checks import KEY_FIX, _classify, platform_check, provider_probes
from sbam_api.preflight.router import run_all, worst
from sbam_contracts.preflight import CheckStatus, PreflightCheck

REAL_CLIENT = httpx.AsyncClient


def settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite://",
        "temporal_host": "temporal:7233",
        "llm_provider": "bifrost",
        "bifrost_url": AnyHttpUrl("http://bifrost:8080"),
    }
    return Settings.model_construct(**{**base, **over})  # no env needed: only the fields we read


def gateway(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        out: httpx.Response = handler(request)
        return out

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(respond), **kw),
    )
    return seen


@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_refused_key_is_down_with_the_fix(status: int) -> None:
    check = _classify("embeddings", "Embeddings", status, "")
    assert check.status is CheckStatus.DOWN and check.reason == "credentials_refused"
    assert check.fix == KEY_FIX


def test_bifrosts_502_credentials_exhausted_is_the_same_failure() -> None:
    body = '{"error":{"type":"upstream_credentials_exhausted"}}'
    assert _classify("x", "X", 502, body).reason == "credentials_refused"


def test_other_statuses_are_graded_not_blamed_on_the_key() -> None:
    assert _classify("x", "X", 200, "").status is CheckStatus.OK
    assert _classify("x", "X", 429, "").status is CheckStatus.DEGRADED
    assert _classify("x", "X", 503, "").status is CheckStatus.DEGRADED
    assert _classify("x", "X", 400, "").status is CheckStatus.DOWN
    assert "credentials" not in (_classify("x", "X", 500, "").reason or "")


def test_the_real_probes_hit_the_gateway_and_report_a_dead_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = gateway(monkeypatch, lambda r: httpx.Response(401, json={"error": {"message": "sk-x"}}))
    probes = provider_probes(settings())
    out = [asyncio.run(p()) for p in probes.values()]
    assert [c.status for c in out] == [CheckStatus.DOWN, CheckStatus.DOWN]
    assert {r.url.path for r in seen} == {"/v1/embeddings", "/v1/chat/completions"}
    chat = next(r for r in seen if r.url.path.endswith("completions"))
    assert b'"max_tokens":1' in chat.content or b'"max_tokens": 1' in chat.content  # cheap
    assert "sk-x" not in out[0].model_dump_json()  # the provider's message never reaches the page


def test_an_unreachable_gateway_is_down_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    gateway(monkeypatch, boom)
    check = asyncio.run(provider_probes(settings())["embeddings"]())
    assert check.status is CheckStatus.DOWN and check.reason == "gateway_unreachable"


def test_in_mock_mode_nothing_is_called_and_the_checks_say_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = gateway(monkeypatch, lambda r: httpx.Response(500))
    out = [asyncio.run(p()) for p in provider_probes(settings(llm_provider="mock")).values()]
    assert [c.status for c in out] == [CheckStatus.MOCK, CheckStatus.MOCK] and seen == []


def test_a_platform_check_that_raises_or_times_out_is_down() -> None:
    async def fine() -> bool:
        return True

    async def raises() -> bool:
        raise RuntimeError("secret host name")

    async def refuses() -> bool:
        return False

    assert asyncio.run(platform_check("db", fine)).status is CheckStatus.OK
    for failing in (raises, refuses):
        out = asyncio.run(platform_check("temporal", failing))
        assert out.status is CheckStatus.DOWN and "secret" not in out.model_dump_json()
        assert out.fix


def test_the_overall_status_is_the_worst_check() -> None:
    def c(status: CheckStatus) -> PreflightCheck:
        return PreflightCheck(id="x", name="X", status=status)

    assert worst([]) is CheckStatus.OK
    assert worst([c(CheckStatus.OK), c(CheckStatus.MOCK)]) is CheckStatus.MOCK
    assert worst([c(CheckStatus.MOCK), c(CheckStatus.DEGRADED), c(CheckStatus.OK)]) is (
        CheckStatus.DEGRADED
    )
    assert worst([c(CheckStatus.DEGRADED), c(CheckStatus.DOWN)]) is CheckStatus.DOWN


def test_run_all_combines_platform_and_provider_checks() -> None:
    async def up() -> bool:
        return True

    async def probe() -> PreflightCheck:
        return PreflightCheck(id="chat_model", name="Model", status=CheckStatus.DOWN, reason="r")

    report = asyncio.run(run_all({"db": up}, {"chat_model": probe}))
    assert report.status is CheckStatus.DOWN
    assert [c.id for c in report.checks] == ["db", "chat_model"]


def test_the_answer_is_cached_so_a_polling_page_bills_nothing_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sbam_api.core.auth import admin_or_fde

    router_module = importlib.import_module("sbam_api.preflight.router")

    calls = {"probe": 0}

    async def ok() -> bool:
        return True

    def probes(_: Settings) -> dict[str, Any]:
        async def probe() -> PreflightCheck:
            calls["probe"] += 1
            return PreflightCheck(id="chat_model", name="Model", status=CheckStatus.OK)

        return {"chat_model": probe}

    monkeypatch.setattr(router_module, "provider_probes", probes)
    app = FastAPI()
    app.state.settings = settings()
    app.state.health_checks = {"db": ok}
    app.include_router(router_module.router)
    app.dependency_overrides[admin_or_fde] = lambda: object()
    client = TestClient(app)
    first = client.get("/preflight")
    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert [c["id"] for c in first.json()["checks"]] == ["db", "chat_model"]
    assert client.get("/preflight").json() == first.json()
    assert calls["probe"] == 1  # the second request was answered from the cache
    app.state.preflight_cache = (app.state.preflight_cache[0] - 61, app.state.preflight_cache[1])
    client.get("/preflight")
    assert calls["probe"] == 2  # a minute later it asks again
