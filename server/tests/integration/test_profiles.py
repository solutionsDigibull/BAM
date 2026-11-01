"""Optional Compose profiles (E1.11): obs, analytics, sandbox-local on top of core (TD §20.3).

Each test skips unless its profile is running (`make up PROFILES=...`), so `make test-int` against
plain core, and CI's compose-smoke, are unaffected. Ports come from the env vars in compose/.env.
"""

import base64
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from .conftest import ENV_FILE, port

pytestmark = pytest.mark.integration

PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "sbam")
OBS = ["otel-collector", "langfuse-web", "langfuse-worker", "clickhouse", "redis", "minio"]


def docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", *args],  # noqa: S607 - docker from PATH, like conftest
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def container(service: str) -> str | None:
    out = docker(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={PROJECT}",
        "--filter",
        f"label=com.docker.compose.service={service}",
    ).stdout.split()
    return out[0] if out else None


def health(service: str) -> str | None:
    cid = container(service)
    if not cid:
        return None
    return docker("inspect", "-f", "{{.State.Health.Status}}", cid).stdout.strip() or None


def wait_healthy(services: list[str], timeout: float = 300) -> dict[str, str | None]:
    deadline = time.monotonic() + timeout
    while True:
        states = {s: health(s) for s in services}
        if all(v == "healthy" for v in states.values()) or time.monotonic() > deadline:
            return states
        time.sleep(5)


def env_value(name: str, default: str | None = None) -> str:
    """A value from the stack's env file (secrets are never hard-coded in tests); `default` when the
    name is absent or empty, else the test fails."""
    for line in Path(ENV_FILE).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip()
            if value or default is None:
                return value
    if default is not None:
        return default
    pytest.fail(f"{name} missing from {ENV_FILE}")


def http(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> int:
    req = urllib.request.Request(url, data=data, headers=headers or {})  # noqa: S310 - localhost
    try:
        with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310
            return int(res.status)
    except urllib.error.HTTPError as e:
        return e.code


def require(service: str) -> None:
    if not container(service):
        pytest.skip(f"{service} not running (profile not started)")


def test_e1_11_t01_obs_healthy_and_trace_visible_in_grafana() -> None:
    require("otel-collector")
    assert wait_healthy(OBS) == dict.fromkeys(OBS, "healthy")

    trace_id = secrets.token_hex(16)
    span = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "e1-11-t01"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": secrets.token_hex(8),
                                "name": "e1_11_t01",
                                "kind": 1,
                                "startTimeUnixNano": str(time.time_ns() - 1_000_000),
                                "endTimeUnixNano": str(time.time_ns()),
                            }
                        ]
                    }
                ],
            }
        ]
    }
    otlp = f"http://127.0.0.1:{port('OTLP_HTTP_PORT', 4318)}/v1/traces"
    assert (
        http(otlp, data=json.dumps(span).encode(), headers={"content-type": "application/json"})
        == 200
    )

    auth = base64.b64encode(f"admin:{env_value('GRAFANA_ADMIN_PASSWORD')}".encode()).decode()
    tempo = "api/datasources/proxy/uid/tempo/api/traces"
    grafana = f"http://127.0.0.1:{port('GRAFANA_PORT', 3002)}/{tempo}/{trace_id}"
    deadline = time.monotonic() + 90
    status = 0
    while time.monotonic() < deadline:
        status = http(grafana, headers={"authorization": f"Basic {auth}"})
        if status == 200:
            break
        time.sleep(3)
    assert status == 200, f"trace {trace_id} not queryable in Grafana/Tempo (last status {status})"


def test_e1_11_t03_posthog_reachable() -> None:
    require("posthog")
    assert wait_healthy(["posthog"], timeout=60) == {"posthog": "healthy"}
    assert http(f"http://127.0.0.1:{port('POSTHOG_PORT', 8010)}/_health/") == 200


def test_e1_11_t04_sandbox_has_no_egress_but_reaches_mock_api() -> None:
    require("sandbox-runner")
    cid = container("sandbox-runner")
    assert cid
    # The runner is the skill-runtime image (no wget): Python's urllib, 5 s per attempt. A DNS
    # failure or a connect error both mean "no route out"; any HTTP answer, even an error, means
    # "reached".
    probe = (
        "import sys,urllib.request,urllib.error\n"
        "try:\n    urllib.request.urlopen(sys.argv[1], timeout=5)\n"
        "except urllib.error.HTTPError:\n    pass\n"
    )

    def reach(url: str) -> subprocess.CompletedProcess[str]:
        return docker("exec", cid, "python", "-c", probe, url)

    assert reach("https://example.org").returncode != 0, "sandbox-runner reached the internet"
    assert (
        reach("http://postgres:5432").returncode != 0
    ), "sandbox-runner reached a core service other than sbam-mock-api"
    mock = reach("http://sbam-mock-api:8090/health")
    assert mock.returncode == 0, mock.stderr
    mode = docker("exec", cid, "printenv", "SANDBOX_MODE").stdout.strip()
    assert mode == "LOCAL_DOCKER_NONCERT"


def test_e1_11_t06_core_unaffected_when_obs_is_down() -> None:
    require("otel-collector")
    stopped = [s for s in ["otel-collector", "langfuse-web", "langfuse-worker"] if container(s)]
    cids = [c for c in (container(s) for s in stopped) if c]
    assert docker("stop", *cids, timeout=120).returncode == 0
    try:
        api = f"http://127.0.0.1:{port('API_PORT', 8000)}/health/ready"
        assert [http(api) for _ in range(20)] == [200] * 20
    finally:
        docker("start", *cids, timeout=120)
        assert wait_healthy(stopped) == dict.fromkeys(stopped, "healthy")
