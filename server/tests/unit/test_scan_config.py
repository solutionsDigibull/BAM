"""E4.3: the signature age limit lives in places that must agree (found by testing against a real
ClamAV: it publishes one database a day, so the scan's limit is 48 h, not 24 h). The worker and the
compose default refuse at the same 48 h; the operator alert (E16.1's `rules.yaml`, the one alert
provisioned, #793) must fire no later than that, so an operator hears before uploads start failing.
"""

import re
from pathlib import Path

from sbam_workers.settings import WorkerSettings
from test_grafana_provisioning import ALERTING, rule, rule_expr, threshold

REPO = Path(__file__).resolve().parents[3]
LIMIT_HOURS = 48


def test_e4_3_the_worker_default_is_48_hours() -> None:
    settings = WorkerSettings(  # type: ignore[call-arg]
        _env_file=None, temporal_host="t:7233", database_url="postgresql://x"
    )
    assert settings.clamav_max_signature_age_hours == LIMIT_HOURS


def test_e4_3_compose_defaults_to_the_same_limit() -> None:
    compose = (REPO / "compose/compose.yaml").read_text(encoding="utf-8")
    match = re.search(
        r"CLAMAV_MAX_SIGNATURE_AGE_HOURS: \$\{CLAMAV_MAX_SIGNATURE_AGE_HOURS:-(\d+)\}", compose
    )
    assert match and int(match.group(1)) == LIMIT_HOURS


def test_e4_3_the_alert_fires_before_uploads_are_refused() -> None:
    clam = rule("sbam-clamav-signatures-stale")
    kind, seconds = threshold(clam)
    assert kind == "gt" and 0 < seconds <= LIMIT_HOURS * 3600
    assert "clamav_signature_age_seconds" in rule_expr(clam)


def test_793_the_signature_alert_is_provisioned_once() -> None:
    """One rule with this uid, and nothing mounted inside the read-only alerting folder."""
    uids = [
        line
        for path in ALERTING.glob("*.yaml")
        for line in path.read_text(encoding="utf-8").splitlines()
        if "uid: sbam-clamav-signatures-stale" in line
    ]
    assert len(uids) == 1
    obs = (REPO / "compose/compose.obs.yaml").read_text(encoding="utf-8")
    inside = re.findall(r"^\s*-\s*\S+:/otel-lgtm/grafana/conf/provisioning/alerting/\S*", obs, re.M)
    assert inside == [], "a file mounted inside the read-only alerting folder cannot be created"
    assert not (REPO / "compose/obs").exists()
