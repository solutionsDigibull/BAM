"""Grafana dashboards and operator alerts as code (E16.1, TD §17.3). No Docker, no running stack.

Checks that everything under compose/grafana parses, that provisioning paths match the obs compose
mounts, that every query uses only metrics (and labels) listed in
compose/grafana/metrics-catalog.yaml, that the catalog's "emitted"/"planned" claims match the code,
and that each TD §17.3 alert exists with the specified threshold. Whether the panels show data and
the alerts fire is checked by tests/integration/test_grafana_alerts.py.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
GRAFANA = REPO / "compose/grafana"
DASHBOARDS = sorted((GRAFANA / "dashboards").glob("*.json"))
ALERTING = GRAFANA / "provisioning/alerting"
LGTM_PROVISIONING = "/otel-lgtm/grafana/conf/provisioning"
DATASOURCE_UIDS = {"prometheus", "tempo", "loki"}  # provisioned by grafana/otel-lgtm
# Label name tokens that would carry personal data, document content or unbounded ids.
FORBIDDEN_LABEL_TOKENS = {
    "user",
    "email",
    "file",
    "document",
    "content",
    "project",
    "run",
    "workflow",
    "ip",
    "phone",
}

# PromQL words that are not metric names.
PROMQL_WORDS = {
    "sum", "min", "max", "avg", "count", "topk", "bottomk", "rate", "irate", "increase", "time",
    "histogram_quantile", "absent", "absent_over_time", "clamp_min", "clamp_max", "abs", "ceil",
    "floor", "round", "vector", "scalar", "label_replace", "max_over_time", "min_over_time",
    "avg_over_time", "last_over_time", "by", "without", "on", "ignoring", "group_left",
    "group_right", "and", "or", "unless", "bool", "offset",
}  # fmt: skip


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def catalog() -> dict[str, Any]:
    doc = load_yaml(GRAFANA / "metrics-catalog.yaml")
    assert isinstance(doc, dict)
    return doc


def catalog_by_name() -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in catalog()["metrics"]}


def obs_service() -> dict[str, Any]:
    compose = load_yaml(REPO / "compose/compose.obs.yaml")
    service: dict[str, Any] = compose["services"]["otel-collector"]
    return service


def rules() -> list[dict[str, Any]]:
    doc = load_yaml(ALERTING / "rules.yaml")
    out: list[dict[str, Any]] = []
    for group in doc["groups"]:
        out.extend(group["rules"])
    return out


def rule(uid: str) -> dict[str, Any]:
    return next(r for r in rules() if r["uid"] == uid)


def rule_expr(r: dict[str, Any]) -> str:
    return str(next(q for q in r["data"] if q["refId"] == "A")["model"]["expr"])


def promql_refs(expr: str) -> tuple[set[str], set[str]]:
    """(metric names, label names) used by a PromQL expression. Deliberately small: it only needs to
    understand the selectors, `by (...)` / `on (...)` clauses and functions these dashboards use."""
    text = re.sub(r'"[^"]*"', '""', expr)
    labels: set[str] = set()
    for sel in re.findall(r"\{([^}]*)\}", text):
        labels |= set(re.findall(r"([A-Za-z_]\w*)\s*(?:=~|!~|!=|=)", sel))
    text = re.sub(r"\{[^}]*\}", "", text)
    clause = r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\(([^)]*)\)"
    for group in re.findall(clause, text):
        labels |= {x.strip() for x in group.split(",") if x.strip()}
    text = re.sub(clause, " ", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    names = set(re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", text)) - PROMQL_WORDS
    return names, labels


def dashboard_queries() -> list[tuple[str, str, str]]:
    """(dashboard uid, panel title, expr) for every target of every panel."""
    out = []
    for path in DASHBOARDS:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for panel in doc["panels"]:
            for target in panel.get("targets", []):
                out.append((doc["uid"], panel["title"], target["expr"]))
    return out


def all_queries() -> list[tuple[str, str]]:
    found = [(f"dashboard {uid} / {title}", expr) for uid, title, expr in dashboard_queries()]
    found += [(f"alert {r['uid']}", rule_expr(r)) for r in rules()]
    return found


# --- dashboards -----------------------------------------------------------------------------------


def test_e16_1_t05_dashboards_parse_and_are_well_formed() -> None:
    assert len(DASHBOARDS) == 6
    uids = set()
    for path in DASHBOARDS:
        doc = json.loads(path.read_text(encoding="utf-8"))  # raises on bad JSON
        assert doc["uid"] == path.stem, f"{path.name}: uid must equal the file name"
        assert doc["uid"] not in uids
        uids.add(doc["uid"])
        assert doc["title"].startswith("SBAM / ")
        assert "sbam" in doc["tags"]
        assert doc["editable"] is False
        assert doc["panels"], path.name
        ids = [p["id"] for p in doc["panels"]]
        assert len(ids) == len(set(ids)), f"{path.name}: duplicate panel ids"
        for panel in doc["panels"]:
            where = f"{path.name} / {panel['title']}"
            assert panel["datasource"]["uid"] in DATASOURCE_UIDS, where
            assert panel["targets"], where
            for target in panel["targets"]:
                assert target["datasource"]["uid"] in DATASOURCE_UIDS, where
                assert target["expr"].strip(), where
                assert target["expr"].count("(") == target["expr"].count(")"), where
                assert target["expr"].count("{") == target["expr"].count("}"), where


def test_e16_1_dashboards_cover_every_td_17_3_view() -> None:
    titles = {
        json.loads(p.read_text(encoding="utf-8"))["uid"]: " | ".join(
            panel["title"].lower() for panel in json.loads(p.read_text(encoding="utf-8"))["panels"]
        )
        for p in DASHBOARDS
    }
    required = {
        "sbam-api-health": ["5xx ratio", "p95 latency"],
        "sbam-temporal-queues": ["schedule-to-start"],
        "sbam-pipeline": ["pipeline throughput", "gate pass rate per gate", "correction rate"],
        "sbam-sandbox": ["success rate", "run duration", "sandbox host"],
        "sbam-llm": ["latency p95 by alias", "cost by alias"],
        "sbam-platform": ["clamav signature age", "disk usage", "frozen-hash"],
    }
    for uid, needles in required.items():
        for needle in needles:
            assert needle in titles[uid], f"{uid} has no panel about '{needle}'"


# --- metrics: the catalog is the contract between code and queries --------------------------------


def test_e16_1_queries_use_only_cataloged_metrics_and_labels() -> None:
    by_name = catalog_by_name()
    problems = []
    for where, expr in all_queries():
        names, labels = promql_refs(expr)
        unknown = names - by_name.keys()
        if unknown:
            problems.append(f"{where}: metric(s) not in metrics-catalog.yaml: {sorted(unknown)}")
        known = {"le"}.union(*(set(by_name[n]["labels"]) for n in names if n in by_name))
        if labels - known:
            problems.append(f"{where}: label(s) {sorted(labels - known)} not on {sorted(names)}")
    assert not problems, "\n".join(problems)


def test_e16_1_every_cataloged_metric_is_used_by_a_dashboard_or_alert() -> None:
    used: set[str] = set()
    for _, expr in all_queries():
        used |= promql_refs(expr)[0]
    assert set(catalog_by_name()) - used == set(), "catalog entries nothing queries"


def test_e16_1_catalog_status_matches_the_code() -> None:
    code = "\n".join(
        p.read_text(encoding="utf-8")
        for root in ("api", "workers", "contracts")
        for p in (REPO / "server" / root).rglob("*.py")
        if ".venv" not in p.parts
    )
    for name, m in catalog_by_name().items():
        if m["status"] == "emitted":
            evidence = m["evidence"]
            assert evidence["contains"] in (REPO / evidence["file"]).read_text(
                encoding="utf-8"
            ), name
        else:
            assert m["status"] == "planned", name
            assert m["owner"] and m["emitter"], f"{name}: planned metrics name an owner and emitter"
            base = re.sub(r"(_bucket|_total|_seconds)+$", "", name)
            spellings = {name, base, base.replace("_", ".")}
            hits = [s for s in spellings if s in code]
            assert (
                not hits
            ), f"{name} now appears in server/ ({hits}): mark it emitted, with evidence"


def test_e16_1_t_security_metric_labels_hold_no_personal_data_or_unbounded_ids() -> None:
    allowed = set(catalog()["label_allowlist"])
    assert "tenant_id" in allowed  # tenant ids are allowed as labels (issue safety check)
    bad = [label for label in allowed if FORBIDDEN_LABEL_TOKENS & set(label.split("_"))]
    assert not bad, f"personal-data or unbounded-id labels: {bad}"
    for name, m in catalog_by_name().items():
        assert set(m["labels"]) <= allowed, f"{name}: labels outside the allowlist"


# --- provisioning paths ---------------------------------------------------------------------------


def test_e16_1_provisioning_paths_match_the_obs_compose_mounts() -> None:
    mounts = {}
    for entry in obs_service()["volumes"]:
        src, _, rest = str(entry).partition(":")
        if src.startswith("./grafana"):
            dst, _, mode = rest.partition(":")
            assert mode == "ro", f"{entry}: provisioning mounts are read-only"
            assert (REPO / "compose" / src).exists(), f"{entry}: source missing"
            mounts[dst] = REPO / "compose" / src
    provider_file = LGTM_PROVISIONING + "/dashboards/sbam.yaml"
    assert mounts[provider_file] == GRAFANA / "provisioning/dashboards/sbam.yaml"
    provider = load_yaml(GRAFANA / "provisioning/dashboards/sbam.yaml")["providers"][0]
    dashboards_dst = provider["options"]["path"]
    assert dashboards_dst.startswith(LGTM_PROVISIONING + "/dashboards/")
    assert mounts[dashboards_dst] == GRAFANA / "dashboards"
    assert provider["allowUiUpdates"] is False and provider["disableDeletion"] is True
    assert mounts[LGTM_PROVISIONING + "/alerting"] == ALERTING
    assert sorted(p.name for p in ALERTING.iterdir()) == [
        "contact-points.yaml",
        "policies.yaml",
        "rules.yaml",
    ]


def test_e16_1_alerting_files_parse_and_reference_a_defined_contact_point() -> None:
    contact = load_yaml(ALERTING / "contact-points.yaml")["contactPoints"]
    names = {c["name"] for c in contact}
    policy = load_yaml(ALERTING / "policies.yaml")["policies"][0]
    routed = {policy["receiver"], *(r["receiver"] for r in policy.get("routes", []))}
    assert routed <= names
    assert contact[0]["receivers"][0]["type"] == "webhook"


def test_e16_1_t_security_env_references_are_declared_and_dollars_escaped() -> None:
    environment = obs_service()["environment"]
    declared = set(environment)
    for path in sorted(ALERTING.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        refs = set(re.findall(r"(?<!\$)\$\{(\w+)\}", text))
        assert (
            refs <= declared
        ), f"{path.name}: {sorted(refs - declared)} not passed to the container"
        # Grafana expands every $VAR in provisioning files: a template's $labels must be $$labels.
        bare = re.findall(r"(?<!\$)\$(?!\{)[A-Za-z_]\w*", text)
        assert not bare, f"{path.name}: unescaped $ (write $$): {bare}"
    env_example = (REPO / "compose/.env.example").read_text(encoding="utf-8")
    assert re.search(r"^GRAFANA_ALERT_WEBHOOK_URL=http://127\.0\.0\.1:", env_example, re.M)
    # The webhook default must be a discard address: a real URL is a secret and never lives in Git.
    default = environment["GRAFANA_ALERT_WEBHOOK_URL"]
    assert ":-http://127.0.0.1:" in default


# --- alerts ---------------------------------------------------------------------------------------


def threshold(r: dict[str, Any]) -> tuple[str, float]:
    cond = next(q for q in r["data"] if q["refId"] == r["condition"])["model"]["conditions"][0]
    return str(cond["evaluator"]["type"]), float(cond["evaluator"]["params"][0])


def minutes(text: str) -> float:
    n, unit = int(text[:-1]), text[-1]
    return n * {"s": 1 / 60, "m": 1, "h": 60}[unit]


def test_e16_1_alert_rules_are_well_formed() -> None:
    seen = set()
    for r in rules():
        uid = r["uid"]
        assert uid not in seen
        seen.add(uid)
        refs = {q["refId"] for q in r["data"]}
        assert r["condition"] in refs
        for q in r["data"]:
            assert q["datasourceUid"] in {"prometheus", "__expr__"}, uid
        assert r["labels"]["severity"] in {"critical", "warning"}, uid
        assert {"summary", "description", "runbook"} <= set(r["annotations"]), uid
        assert r["noDataState"] in {"OK", "Alerting"}, uid
        assert r["execErrState"] == "Error", uid
        assert r["isPaused"] is False, uid
        assert "for" in r, f"{uid}: no `for` period"
    group = load_yaml(ALERTING / "rules.yaml")["groups"][0]
    assert minutes(group["interval"]) <= 1


def test_e16_1_td_17_3_alert_thresholds() -> None:
    """The seven operator alerts, each with the threshold and window the design specifies."""
    five_xx = rule("sbam-api-5xx-ratio")
    assert threshold(five_xx) == ("gt", 2) and "[5m]" in rule_expr(five_xx)
    sched = rule("sbam-temporal-schedule-to-start")
    assert threshold(sched) == ("gt", 120) and "schedule_to_start" in rule_expr(sched)
    infra = rule("sbam-sandbox-infra-errors")
    assert threshold(infra) == ("gt", 10) and "[30m]" in rule_expr(infra)
    assert "infra_error" in rule_expr(infra)
    clam = rule("sbam-clamav-signatures-stale")
    assert threshold(clam) == ("gt", 24 * 3600)
    disk = rule("sbam-disk-usage")
    assert threshold(disk) == ("gt", 80) and 'state="used"' in rule_expr(disk)
    host = rule("sbam-sandbox-host-down")
    assert threshold(host) == ("lt", 1) and minutes(host["for"]) == 2
    assert rule("sbam-frozen-hash-mismatch")["labels"]["severity"] == "critical"


def test_e16_1_t04_frozen_hash_failure_is_critical_and_fires_immediately() -> None:
    r = rule("sbam-frozen-hash-mismatch")
    assert r["labels"]["severity"] == "critical"
    assert threshold(r) == ("gt", 0) and minutes(r["for"]) == 0


def test_e16_1_t01_sandbox_outage_alerts_within_five_minutes() -> None:
    """Host down entirely means no runs, so no infra-error rate: the probe gauge carries it."""
    r = rule("sbam-sandbox-host-down")
    interval = minutes(load_yaml(ALERTING / "rules.yaml")["groups"][0]["interval"])
    assert r["noDataState"] == "Alerting"  # a probe that stops reporting is also an outage
    assert "sbam_sandbox_host_up" in rule_expr(r)
    assert (
        minutes(r["for"]) + interval < 5
    )  # `for` + one evaluation leaves room for the probe period


def test_e16_1_silence_is_an_alert_where_silence_is_the_failure() -> None:
    """Collector down and dead nightly job must not look healthy."""
    assert rule("sbam-telemetry-missing")["labels"]["severity"] == "warning"
    assert "absent_over_time" in rule_expr(rule("sbam-telemetry-missing"))
    assert rule("sbam-frozen-hash-verify-stale")["noDataState"] == "Alerting"
    assert rule("sbam-clamav-signatures-stale")["noDataState"] == "Alerting"


def test_e16_1_flapping_is_damped() -> None:
    for r in rules():
        if r["uid"] != "sbam-frozen-hash-mismatch":  # an integrity failure pages at once
            assert minutes(r["for"]) >= 1, f"{r['uid']} needs a `for` of at least 1 minute"
    policy = load_yaml(ALERTING / "policies.yaml")["policies"][0]
    assert minutes(policy["group_interval"]) >= 5 and minutes(policy["repeat_interval"]) >= 60


# --- access ---------------------------------------------------------------------------------------


def test_e16_1_t06_grafana_is_not_publicly_reachable() -> None:
    svc = obs_service()
    # Anonymous access is Admin by default in grafana/otel-lgtm: it must be off.
    assert str(svc["environment"]["GF_AUTH_ANONYMOUS_ENABLED"]).lower() == "false"
    assert str(svc["environment"]["GF_USERS_ALLOW_SIGN_UP"]).lower() == "false"
    for p in svc["ports"]:
        assert str(p).startswith(
            "127.0.0.1:"
        ), f"{p} must bind to loopback only (Netbird for remote)"
    assert "GRAFANA_ADMIN_PASSWORD" in str(svc["environment"]["GF_SECURITY_ADMIN_PASSWORD"])


@pytest.mark.parametrize("path", [*DASHBOARDS, *ALERTING.glob("*.yaml")], ids=lambda p: p.name)
def test_e16_1_no_secrets_in_provisioned_files(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"hooks\.slack\.com|https://[^\s\"']*(token|secret|key)=", text, re.I)
    assert not re.search(r"password\s*:\s*[^\s$]", text, re.I)
