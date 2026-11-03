"""N-02b (#609): the bulkhead saturation `/health/ready` reports is also a gauge the alerts read."""

from contextlib import ExitStack

from opentelemetry.metrics import CallbackOptions
from sbam_api.core.bulkhead import KEYCLOAK_ADMIN, REGISTRY
from sbam_api.core.bulkhead_metrics import observe


def reading() -> dict[str, int]:
    options = CallbackOptions()
    return {str(o.attributes["bulkhead"]): int(o.value) for o in observe(options)}  # type: ignore[index]


def test_n02b_every_registered_bulkhead_is_observed_and_reads_zero_when_idle() -> None:
    assert reading() == {b.name: 0 for b in REGISTRY}
    assert {"clamd", "keycloak_admin", "keycloak_jwks"} <= set(reading())


def test_n02b_a_saturated_bulkhead_reads_one_and_the_others_stay_zero() -> None:
    with ExitStack() as stack:
        for _ in range(KEYCLOAK_ADMIN.limit):
            stack.enter_context(KEYCLOAK_ADMIN.slot())
        seen = reading()
        assert seen["keycloak_admin"] == 1
        assert seen["clamd"] == 0 and seen["keycloak_jwks"] == 0
    assert reading()["keycloak_admin"] == 0  # the slots were given back


def test_n02b_the_metric_name_and_label_are_the_ones_the_alert_queries() -> None:
    from pathlib import Path

    rules = Path(__file__).resolve().parents[3] / "compose/grafana/provisioning/alerting/rules.yaml"
    assert "max by (bulkhead) (sbam_bulkhead_saturated)" in rules.read_text(encoding="utf-8")
