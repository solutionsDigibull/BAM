"""WG-38 (#925): /health/ready says whether the skill-runtime registry is loaded, without ever
failing the probe (a fresh stack is healthy, it just cannot build yet)."""

from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from registry_db import sample_registry, sqlite_sessions
from sbam_api.core.conventions import ApiError
from sbam_api.core.health import health_router
from sbam_api.registry.preflight import prerequisite_checks, require_build_prerequisites
from sbam_api.registry.sync import sync


def fixed(value: bool) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return value

    return check


def ready(sessions: object) -> tuple[int, dict[str, object]]:
    app = FastAPI()
    app.include_router(
        health_router({"db": fixed(True)}, prerequisite_checks(lambda: sessions))  # type: ignore[arg-type,return-value]
    )
    response = TestClient(app).get("/health/ready")
    return response.status_code, response.json()


def test_wg38_t14_readiness_reports_a_missing_registry_and_stays_ready() -> None:
    sessions = sqlite_sessions()
    status, body = ready(sessions)
    assert status == 200 and body["status"] == "ok"
    assert body["prerequisites"] == {"runtime_registry": "missing"}
    sync(sessions, sample_registry(), activate=True)
    assert ready(sessions)[1]["prerequisites"] == {"runtime_registry": "ok"}


def test_wg38_t15_an_unreachable_database_is_unknown_not_a_failed_probe() -> None:
    def broken() -> object:
        raise RuntimeError("no database")

    status, body = ready(broken)
    assert status == 200 and body["prerequisites"] == {"runtime_registry": "unknown"}


def test_wg38_t16_no_info_lines_means_the_old_body() -> None:
    app = FastAPI()
    app.include_router(health_router({"db": fixed(True)}))
    assert "prerequisites" not in TestClient(app).get("/health/ready").json()


def test_wg38_t17_the_preflight_names_the_fix_and_passes_once_loaded() -> None:
    sessions = sqlite_sessions()
    with sessions() as session, pytest.raises(ApiError) as refused:
        require_build_prerequisites(session)
    assert refused.value.status == 409
    assert "make runtime-registry" in (refused.value.detail or "")
    sync(sessions, sample_registry(), activate=True)
    with sessions() as session:
        require_build_prerequisites(session)
