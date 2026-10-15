"""E4.3: the signature age limit lives in three places that must move together (found by testing
against a real ClamAV: it publishes one database a day, so the limit is 48 h, not 24 h)."""

import re
from pathlib import Path

from sbam_workers.settings import WorkerSettings

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


def test_e4_3_the_alert_fires_at_the_same_limit() -> None:
    rule = (REPO / "compose/obs/alerting/clamav.yaml").read_text(encoding="utf-8")
    match = re.search(r"evaluator: \{ type: gt, params: \[(\d+)\] \}", rule)
    assert match and int(match.group(1)) == LIMIT_HOURS * 3600
    assert f"older than {LIMIT_HOURS} hours" in rule
