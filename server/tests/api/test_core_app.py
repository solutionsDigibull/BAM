"""E1.3 API tests for the FastAPI core: health, problem+json, correlation ids, logs, body limit."""

import json
import socket
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sbam_api.core.conventions import (
    IdempotencyScope,
    InMemoryIdempotencyStore,
    body_hash,
    replay_run_id,
)
from sbam_api.core.health import Check, default_checks
from sbam_api.core.settings import Settings, load_settings
from sbam_api.main import create_app, discover_routers
from sbam_contracts.api import PROBLEM_JSON, ProblemDetail


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://sbam@127.0.0.1:1/sbam",
        "storage_root": tmp_path,
        "temporal_host": "127.0.0.1:1",
        "keycloak_issuer": "http://127.0.0.1:1/realms/sbam",
        "keycloak_audience": "sbam-api",
        **overrides,
    }
    return load_settings(_env_file=None, **values)


def fixed(result: bool) -> Check:
    async def check() -> bool:
        return result

    return check


ALL_OK: Mapping[str, Check] = {
    n: fixed(True) for n in ("db", "storage", "temporal", "keycloak_jwks")
}

AppFactory = Callable[..., FastAPI]


@pytest.fixture
def app_factory(tmp_path: Path) -> AppFactory:
    def build(checks: Mapping[str, Check] | None = None, **settings: object) -> FastAPI:
        return create_app(make_settings(tmp_path, **settings), ALL_OK if checks is None else checks)

    return build


def log_lines(out: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def assert_problem(response_json: dict[str, object], status: int) -> ProblemDetail:
    problem = ProblemDetail.model_validate(response_json)
    assert problem.status == status
    return problem


# --- Health ---------------------------------------------------------------------------------


def test_e1_3_t01_liveness(app_factory: AppFactory) -> None:
    response = TestClient(app_factory()).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_e1_3_t02_ready_when_all_dependencies_up(app_factory: AppFactory) -> None:
    response = TestClient(app_factory()).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {
        "db": "ok",
        "storage": "ok",
        "temporal": "ok",
        "keycloak_jwks": "ok",
    }


def test_e1_3_t03_ready_503_when_temporal_down(app_factory: AppFactory) -> None:
    checks = {**ALL_OK, "temporal": fixed(False)}
    response = TestClient(app_factory(checks)).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "fail",
        "checks": {"db": "ok", "storage": "ok", "temporal": "fail", "keycloak_jwks": "ok"},
    }


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_e1_3_t03_real_checks_fail_on_unreachable_dependencies(tmp_path: Path) -> None:
    port = _closed_port()
    settings = make_settings(
        tmp_path,
        database_url=f"postgresql://sbam@127.0.0.1:{port}/sbam",
        temporal_host=f"127.0.0.1:{port}",
        keycloak_issuer=f"http://127.0.0.1:{port}/realms/sbam",
    )
    checks = default_checks(settings)
    response = TestClient(app_factory_from(settings, checks)).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {
        "db": "fail",
        "storage": "ok",  # tmp_path exists and is writable
        "temporal": "fail",
        "keycloak_jwks": "fail",
    }


def app_factory_from(settings: Settings, checks: Mapping[str, Check]) -> FastAPI:
    return create_app(settings, checks)


def test_e1_3_health_exposes_no_hosts_or_versions(app_factory: AppFactory) -> None:
    body = TestClient(app_factory()).get("/health/ready").text
    for leak in ("127.0.0.1", "postgresql", "realms", "version", sys.version.split()[0]):
        assert leak not in body


# --- problem+json ---------------------------------------------------------------------------


class Item(BaseModel):
    count: int


def with_test_routes(app: FastAPI) -> FastAPI:
    @app.post("/api/v1/_test/items")
    async def create_item(item: Item) -> Item:
        return item

    @app.get("/api/v1/_test/boom")
    async def boom() -> None:
        raise RuntimeError("db password=x")

    return app


def test_e1_3_t04_validation_error_is_422_problem(app_factory: AppFactory) -> None:
    client = TestClient(with_test_routes(app_factory()))
    response = client.post("/api/v1/_test/items", json={"count": "not-a-number"})
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = assert_problem(response.json(), 422)
    assert problem.code == "VALIDATION_ERROR"
    assert problem.errors[0].path == "count"
    assert problem.correlation_id == response.headers["x-correlation-id"]


def test_e1_3_t05_unhandled_exception_is_generic_500(
    app_factory: AppFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    client = TestClient(with_test_routes(app_factory()))
    response = client.get("/api/v1/_test/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = assert_problem(response.json(), 500)
    assert problem.code == "INTERNAL_ERROR"
    assert "password" not in response.text
    logs = capsys.readouterr().out
    assert "password" not in logs
    errors = [line for line in log_lines(logs) if line.get("msg") == "unhandled exception"]
    assert errors and errors[0]["exc_type"] == "RuntimeError"
    assert errors[0]["correlation_id"] == problem.correlation_id


def test_e0_7_t01_problem_responses_use_problem_json(app_factory: AppFactory) -> None:
    response = TestClient(app_factory()).get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert assert_problem(response.json(), 404).code == "NOT_FOUND"


# --- Correlation ids and logs ----------------------------------------------------------------


def test_e1_3_t06_log_injection_is_neutralised(
    app_factory: AppFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    response = TestClient(app_factory()).get(
        "/health/live", headers={"X-Correlation-ID": "abc\r\nlevel=ERROR"}
    )
    cid = response.headers["x-correlation-id"]
    assert cid != "abc\r\nlevel=ERROR"
    assert uuid.UUID(cid).version == 7
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(json.loads(line) for line in lines)  # every line is one JSON object
    assert not any(line.startswith("level=") for line in lines)


def test_e1_3_t08_correlation_id_is_echoed(app_factory: AppFactory) -> None:
    response = TestClient(app_factory()).get(
        "/api/v1/does-not-exist", headers={"X-Correlation-ID": "req-123"}
    )
    assert response.headers["x-correlation-id"] == "req-123"
    assert response.json()["correlation_id"] == "req-123"


def test_e1_3_access_log_fields(
    app_factory: AppFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    TestClient(app_factory()).get("/health/live", headers={"X-Correlation-ID": "req-9"})
    access = [line for line in log_lines(capsys.readouterr().out) if line["msg"] == "request"]
    assert access[-1]["correlation_id"] == "req-9"
    assert access[-1]["route"] == "/health/live" and access[-1]["status"] == 200
    assert {"ts", "level", "tenant_id", "project_id", "duration_ms"} <= access[-1].keys()


def test_e1_3_secrets_redacted_in_logs(
    app_factory: AppFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging

    app_factory()
    capsys.readouterr()
    logging.getLogger("t").warning("call failed Authorization: Bearer abc.def.ghi token=xyz")
    out = capsys.readouterr().out
    assert "abc.def.ghi" not in out and "xyz" not in out


# --- Body limit ------------------------------------------------------------------------------


def test_e1_3_body_over_limit_is_413(app_factory: AppFactory) -> None:
    client = TestClient(with_test_routes(app_factory(max_body_bytes=32)))
    response = client.post(
        "/api/v1/_test/items", content=b'{"count": 1, "pad": "' + b"x" * 64 + b'"}'
    )
    assert response.status_code == 413
    assert assert_problem(response.json(), 413).code == "PAYLOAD_TOO_LARGE"
    assert "x-correlation-id" in response.headers


def test_e1_3_body_under_limit_passes(app_factory: AppFactory) -> None:
    client = TestClient(with_test_routes(app_factory(max_body_bytes=1024)))
    assert client.post("/api/v1/_test/items", json={"count": 3}).json() == {"count": 3}


# --- Router discovery ------------------------------------------------------------------------


def test_e1_3_router_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "fakeapi"
    (pkg / "widgets").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core").mkdir()
    (pkg / "core" / "__init__.py").write_text("router = 'not a router'")
    (pkg / "widgets" / "__init__.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/widgets')\ndef widgets() -> list[int]:\n    return [1]\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import fakeapi  # type: ignore[import-not-found]

    routers = discover_routers(fakeapi)
    assert len(routers) == 1
    from sbam_api.storage import router as storage_router

    assert storage_router in discover_routers()  # the first feature module (#37)


# --- E0.7-T04 hand-off: one start per Idempotency-Key ----------------------------------------


def test_e0_7_t04_idempotent_generate_starts_once(app_factory: AppFactory) -> None:
    app = app_factory()
    store = InMemoryIdempotencyStore()
    starts: list[str] = []

    class GenerateBody(BaseModel):
        sub_skill_ids: list[str] = []

    from fastapi import Header

    @app.post("/api/v1/_test/generate", status_code=202)
    async def generate(
        body: GenerateBody, idempotency_key: str = Header(alias="Idempotency-Key")
    ) -> dict[str, str]:
        scope = IdempotencyScope("t1", "u1", "POST W/generate")
        payload = body.model_dump()
        run_id = replay_run_id(store, scope, idempotency_key, payload)
        if run_id is None:
            run_id = f"run-{len(starts) + 1}"
            starts.append(run_id)  # stands in for the Temporal workflow start
            store.put(scope, idempotency_key, body_hash(payload), run_id)
        return {"run_id": run_id}

    client = TestClient(app)
    headers = {"Idempotency-Key": "gen-0001"}
    first = client.post(
        "/api/v1/_test/generate", json={"sub_skill_ids": ["MS01.1"]}, headers=headers
    )
    second = client.post(
        "/api/v1/_test/generate", json={"sub_skill_ids": ["MS01.1"]}, headers=headers
    )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {"run_id": "run-1"}
    assert starts == ["run-1"]
    reused = client.post("/api/v1/_test/generate", json={"sub_skill_ids": ["X"]}, headers=headers)
    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def _storage_check(root: Path) -> bool:
    return await default_checks(make_settings(root))["storage"]()


def test_e1_3_storage_check_needs_a_writable_directory(tmp_path: Path) -> None:
    import asyncio

    assert asyncio.run(_storage_check(tmp_path)) is True
    assert not list(tmp_path.iterdir())  # probe file cleaned up
    with pytest.raises(OSError):
        asyncio.run(_storage_check(tmp_path / "missing"))
