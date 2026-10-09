"""E1.10 (#39): the Compose core profile is healthy, wired and locked down (TD §20.3)."""

import json
import subprocess
import urllib.request

import pytest

from .conftest import Compose, port

pytestmark = pytest.mark.integration

CORE = [
    "postgres", "keycloak", "temporal", "temporal-ui", "bifrost", "clamav", "tusd", "sbam-api",
    "sbam-mock-api", "sbam-web", "worker-ingest", "worker-knowledge", "worker-llm", "worker-build",
    "worker-test", "worker-certify",
]  # fmt: skip
ONE_OFF = ["storage-init", "temporal-schema", "temporal-namespace", "api-migrate"]


def services(compose: Compose) -> list[dict[str, object]]:
    out = compose("ps", "--all", "--format", "json").stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_e1_10_t01_every_core_service_is_healthy(compose: Compose) -> None:
    state = {s["Service"]: s for s in services(compose)}
    health = {name: state.get(name, {}).get("Health") for name in CORE}
    assert health == dict.fromkeys(CORE, "healthy")
    for job in ONE_OFF:
        assert state[job]["State"] == "exited" and state[job]["ExitCode"] == 0, job


def test_e1_10_t02_all_databases_exist(compose: Compose) -> None:
    sql = "SELECT datname FROM pg_database"
    out = compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-Atc", sql).stdout.split()
    assert {"sbam", "temporal", "temporal_visibility", "keycloak", "langfuse"} <= set(out)


@pytest.mark.parametrize("job", ["storage-init", "temporal-schema", "temporal-namespace"])
def test_e1_10_t03_init_jobs_are_idempotent(compose: Compose, job: str) -> None:
    result = compose("run", "--rm", "--no-deps", job, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]


def test_e1_10_t05_migration_finishes_before_api_starts(compose: Compose) -> None:
    def state(service: str) -> dict[str, object]:
        cid = compose("ps", "--all", "-q", service).stdout.strip()
        argv = ["docker", "inspect", "--format", "{{json .State}}", cid]
        raw = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
            argv, capture_output=True, text=True, check=True
        ).stdout
        result: dict[str, object] = json.loads(raw)
        return result

    migrate, api = state("api-migrate"), state("sbam-api")
    assert migrate["Status"] == "exited" and migrate["ExitCode"] == 0
    # The guarantee: Compose starts sbam-api only after api-migrate completed successfully.
    config = json.loads(compose("config", "--format", "json").stdout)
    gate = config["services"]["sbam-api"]["depends_on"]["api-migrate"]["condition"]
    assert gate == "service_completed_successfully"
    # On a fresh start the timestamps show it too. A later `up` re-runs the one-off job while the
    # running API stays up, so compare only when the API started after this migration run began.
    # RFC 3339 timestamps from the same daemon compare correctly as strings.
    if str(api["StartedAt"]) >= str(migrate["StartedAt"]):
        assert str(migrate["FinishedAt"]) <= str(api["StartedAt"])


def test_e1_10_t06_published_ports_bind_to_localhost_only(compose: Compose) -> None:
    published = [
        (s["Service"], p["URL"])
        for s in services(compose)
        for p in s.get("Publishers") or []  # type: ignore[attr-defined]
        if p.get("PublishedPort")
    ]
    assert published, "no published ports found"
    assert [x for x in published if x[1] != "127.0.0.1"] == []


def test_e1_10_t09_api_ready_inside_the_stack() -> None:
    url = f"http://127.0.0.1:{port('API_PORT', 8000)}/health/ready"
    with urllib.request.urlopen(url, timeout=10) as r:
        body = json.load(r)
    assert r.status == 200
    assert body == {
        "status": "ok",
        "checks": {"db": "ok", "storage": "ok", "temporal": "ok", "keycloak_jwks": "ok"},
    }


def test_e1_10_t11_workers_have_no_internet_but_reach_bifrost(compose: Compose) -> None:
    probe = (
        "import socket,sys\n"
        "def ok(h,p):\n"
        "    try: socket.create_connection((h,p),timeout=5).close(); return True\n"
        "    except OSError: return False\n"
        "print(ok('example.org',443), ok('bifrost',8080))"
    )
    out = compose("exec", "-T", "worker-knowledge", "python", "-c", probe).stdout.split()
    assert out == ["False", "True"]
