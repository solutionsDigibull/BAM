"""E16.1 Grafana dashboards and operator alerts against the real obs stack (Compose `obs` profile).

Skips unless otel-collector is running (`make up PROFILES=obs`). The static checks (files parse,
queries use cataloged metrics, mounts match) are in tests/unit/test_grafana_provisioning.py and need
no stack.

The alert tests push synthetic OTLP/HTTP metrics to the collector and poll Grafana's rule state. The
code does not emit the sandbox, disk or frozen-hash metrics yet (see
compose/grafana/metrics-catalog.yaml),
so these tests are also the contract a future emitter must meet. They use a throwaway service.name
(`sbam-e2e-grafana`) and reset every gauge they raise before returning, but they do write into the
stack's Prometheus: run them on a dev stack, not on a shared environment.

Name translation (OTLP -> Prometheus) is assumed: counters gain `_total`, unitless gauges keep their
name, gauges with unit `By` gain `_bytes`. The first run against a stack should confirm that.
"""

import base64
import json
import time
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import yaml

from .conftest import REPO, port
from .test_profiles import env_value, http, require

pytestmark = pytest.mark.integration

RULES = yaml.safe_load(
    (REPO / "compose/grafana/provisioning/alerting/rules.yaml").read_text(encoding="utf-8")
)["groups"][0]["rules"]
TITLE = {r["uid"]: r["title"] for r in RULES}
SYNTH = "sbam-e2e-grafana"


def grafana_url(path: str) -> str:
    return f"http://127.0.0.1:{port('GRAFANA_PORT', 3002)}{path}"


def grafana_get(path: str) -> Any:
    auth = base64.b64encode(f"admin:{env_value('GRAFANA_ADMIN_PASSWORD')}".encode()).decode()
    req = urllib.request.Request(  # noqa: S310 - localhost
        grafana_url(path), headers={"authorization": f"Basic {auth}"}
    )
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
        return json.loads(res.read())


def push(
    name: str,
    value: float,
    attrs: dict[str, str] | None = None,
    *,
    counter: bool = False,
    unit: str = "",
) -> None:
    """One OTLP/HTTP JSON data point (gauge, or cumulative monotonic sum) to the local collector."""
    point = {
        "timeUnixNano": str(time.time_ns()),
        "startTimeUnixNano": "1700000000000000000",
        "asDouble": value,
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in (attrs or {}).items()],
    }
    kind: dict[str, Any] = (
        {"sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [point]}}
        if counter
        else {"gauge": {"dataPoints": [point]}}
    )
    body = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": SYNTH}}]
                },
                "scopeMetrics": [
                    {"scope": {"name": "e16.1"}, "metrics": [{"name": name, "unit": unit, **kind}]}
                ],
            }
        ]
    }
    status = http(
        f"http://127.0.0.1:{port('OTLP_HTTP_PORT', 4318)}/v1/metrics",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    assert status == 200, f"collector rejected {name}: HTTP {status}"


def rule_state(uid: str) -> str:
    """Grafana-managed rule state: inactive, pending or firing."""
    groups = grafana_get("/api/prometheus/grafana/api/v1/rules")["data"]["groups"]
    for group in groups:
        for rule in group["rules"]:
            if rule["name"] == TITLE[uid]:
                return str(rule["state"])
    pytest.fail(f"rule {uid} not loaded by Grafana")


def wait_for(uid: str, state: str, within_s: float, feed: Callable[[], None]) -> float:
    """Keep feeding the metric until the rule reaches `state`; returns seconds taken."""
    start = time.monotonic()
    while time.monotonic() - start < within_s:
        feed()
        if rule_state(uid) == state:
            return time.monotonic() - start
        time.sleep(10)
    pytest.fail(f"{uid} not {state} after {within_s:.0f}s (last: {rule_state(uid)})")


@pytest.fixture(autouse=True)
def _obs_up() -> Iterator[None]:
    require("otel-collector")
    yield


def test_e16_1_t05_dashboards_and_rules_are_provisioned() -> None:
    found = grafana_get("/api/search?tag=sbam&type=dash-db")
    assert {d["uid"] for d in found} == {
        p.stem for p in (REPO / "compose/grafana/dashboards").glob("*.json")
    }
    provisioned = {r["uid"] for r in grafana_get("/api/v1/provisioning/alert-rules")}
    assert set(TITLE) <= provisioned
    contacts = {c["name"] for c in grafana_get("/api/v1/provisioning/contact-points")}
    assert "sbam-operators" in contacts
    # Manual part of T05 (record in the PR): open each dashboard and confirm the API health panels
    # show data after a few requests to sbam-api; the others stay empty until their emitters land.


def test_e16_1_t06_public_request_to_grafana_is_not_served() -> None:
    # Anonymous requests must not get a session: the lgtm image's default is anonymous Admin.
    assert http(grafana_url("/api/search")) == 401
    assert http(grafana_url("/api/v1/provisioning/alert-rules")) == 401
    # Reachability from off the box is a network property (loopback bind + Netbird, #142).
    # The static half is in the unit test; verify the rest by hand: from a host outside the VPN,
    # <host>:3002 must time out.


def test_e16_1_t01_sandbox_host_outage_alerts_within_five_minutes() -> None:
    uid = "sbam-sandbox-host-down"

    def down() -> None:
        push("sbam_sandbox_host_up", 0.0)

    try:
        push("sbam_sandbox_host_up", 1.0)
        taken = wait_for(uid, "firing", 300, down)
        assert taken < 300
    finally:
        push("sbam_sandbox_host_up", 1.0)


def test_e16_1_t02_api_5xx_at_five_percent_fires_after_six_minutes() -> None:
    uid = "sbam-api-5xx-ratio"
    base = {"service": "sbam-api", "span_kind": "SPAN_KIND_SERVER"}
    total = errors = 0.0

    def feed() -> None:
        nonlocal total, errors
        total += 95
        errors += 5  # 5% of calls fail
        push(
            "traces_spanmetrics_calls",
            total,
            {**base, "status_code": "STATUS_CODE_UNSET"},
            counter=True,
        )
        push(
            "traces_spanmetrics_calls",
            errors,
            {**base, "status_code": "STATUS_CODE_ERROR"},
            counter=True,
        )

    wait_for(uid, "firing", 6.5 * 60 + 120, feed)


def test_e16_1_t03_disk_at_81_percent_fires() -> None:
    uid = "sbam-disk-usage"

    def feed() -> None:
        for state, value in (("used", 81.0), ("free", 19.0)):
            push(
                "system_filesystem_usage", value, {"state": state, "mountpoint": "/e2e"}, unit="By"
            )

    try:
        wait_for(uid, "firing", 6 * 60, feed)
    finally:
        for state, value in (("used", 10.0), ("free", 90.0)):
            push(
                "system_filesystem_usage", value, {"state": state, "mountpoint": "/e2e"}, unit="By"
            )


def test_e16_1_t04_frozen_hash_mismatch_raises_a_critical_alert() -> None:
    uid = "sbam-frozen-hash-mismatch"

    def feed() -> None:
        push("sbam_frozen_hash_mismatches", 1.0)

    try:
        wait_for(uid, "firing", 5 * 60, feed)
        rules = grafana_get("/api/prometheus/grafana/api/v1/rules")["data"]["groups"]
        labels = next(r["labels"] for g in rules for r in g["rules"] if r["name"] == TITLE[uid])
        assert labels["severity"] == "critical"
    finally:
        push("sbam_frozen_hash_mismatches", 0.0)
