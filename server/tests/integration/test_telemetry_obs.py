"""E1.4 OpenTelemetry against the real collector (Compose `obs` profile, #42).

Skips unless otel-collector is running (`make up PROFILES=obs`). sbam-api exports to it because
compose.obs.yaml sets OTEL_EXPORTER_OTLP_ENDPOINT on sbam-api.
"""

import base64
import json
import statistics
import time
import urllib.parse
import urllib.request
import uuid

import pytest

from .conftest import port
from .test_profiles import container, docker, env_value, require, wait_healthy

pytestmark = pytest.mark.integration


def api(path: str, headers: dict[str, str] | None = None) -> tuple[int, float]:
    url = f"http://127.0.0.1:{port('API_PORT', 8000)}{path}"
    req = urllib.request.Request(url, headers=headers or {})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
        return int(res.status), time.perf_counter() - start


def grafana(path: str) -> dict[str, object] | None:
    auth = base64.b64encode(f"admin:{env_value('GRAFANA_ADMIN_PASSWORD')}".encode()).decode()
    url = f"http://127.0.0.1:{port('GRAFANA_PORT', 3002)}/api/datasources/proxy/uid/tempo{path}"
    req = urllib.request.Request(url, headers={"authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
            body: dict[str, object] = json.loads(res.read())
            return body
    except OSError:
        return None


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[18]


def test_e1_4_t01_request_trace_visible_in_collector_with_correlation_id() -> None:
    require("otel-collector")
    cid = f"e14-t01-{uuid.uuid4().hex[:12]}"
    # /health/ready makes an httpx call (Keycloak JWKS): the trace has a server and a client span.
    assert api("/health/ready", {"x-correlation-id": cid})[0] == 200
    query = urllib.parse.quote(f'{{span.sbam.correlation_id="{cid}"}}')
    trace_id = None
    deadline = time.monotonic() + 90  # batch export + Tempo ingestion
    while time.monotonic() < deadline and not trace_id:
        found = grafana(f"/api/search?q={query}&limit=1")
        traces = (found or {}).get("traces") or []
        trace_id = traces[0]["traceID"] if traces else None  # type: ignore[index]
        time.sleep(3)
    assert trace_id, f"no trace with sbam.correlation_id={cid} in Tempo"
    full = grafana(f"/api/traces/{trace_id}") or {}
    dumped = json.dumps(full)
    assert dumped.count('"spanId"') + dumped.count('"spanID"') >= 2
    assert '"sbam-api"' in dumped  # service.name


def best_p95(rounds: list[list[tuple[int, float]]]) -> float:
    """Lowest per-round p95. One 100-request p95 through Docker Desktop swings by over 50% with
    nothing changed; a real exporter slowdown hits every round, so the best round still shows it."""
    return min(p95([t for _, t in r]) for r in rounds)


def test_e1_4_t04_collector_down_requests_still_succeed_without_slowdown() -> None:
    require("otel-collector")
    for _ in range(20):  # warm up connections and the event loop
        api("/health/live")
    baseline = [[api("/health/live") for _ in range(40)] for _ in range(5)]
    cid = container("otel-collector")
    assert cid and docker("stop", cid, timeout=120).returncode == 0
    try:
        time.sleep(2)
        down = [[api("/health/live") for _ in range(40)] for _ in range(5)]
    finally:
        docker("start", cid, timeout=120)
        assert wait_healthy(["otel-collector"]) == {"otel-collector": "healthy"}
    assert [s for r in down for s, _ in r] == [200] * 200
    up_p95, down_p95 = best_p95(baseline), best_p95(down)
    # < 10% increase, with a 5 ms floor so millisecond-level jitter on a laptop isn't a failure.
    assert (
        down_p95 <= up_p95 * 1.10 + 0.005
    ), f"p95 up={up_p95 * 1000:.1f}ms down={down_p95 * 1000:.1f}ms"
