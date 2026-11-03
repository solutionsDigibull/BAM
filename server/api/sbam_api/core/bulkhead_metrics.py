"""`sbam_bulkhead_saturated{bulkhead}`: 1 while every slot of a dependency's bulkhead is taken.

`/health/ready` already reports `saturated` without changing its status code (N-02b); this puts
the same signal where the operator alerts can see it. Exported over OTLP only while
OTEL_EXPORTER_OTLP_ENDPOINT is set (the `obs` profile); otherwise the gauge is a no-op. The callback
only reads in-memory counters, so it never touches a dependency and cannot hang.
"""

from collections.abc import Iterable

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from sbam_api.core.bulkhead import REGISTRY
from sbam_api.core.settings import Settings

EXPORT_INTERVAL_MS = 15_000
_started = False


def observe(_: CallbackOptions) -> Iterable[Observation]:
    for bulkhead in REGISTRY:
        yield Observation(1 if bulkhead.saturated else 0, {"bulkhead": bulkhead.name})


def start(settings: Settings) -> None:
    """Register the gauge once per process (create_app may run many times, in tests)."""
    global _started
    if _started:
        return
    _started = True
    # A MeterProvider may already be set by something else in the process; a second one is refused.
    if settings.otel_exporter_otlp_endpoint and not isinstance(
        metrics.get_meter_provider(), MeterProvider
    ):
        endpoint = str(settings.otel_exporter_otlp_endpoint).rstrip("/")
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=2),
            export_interval_millis=EXPORT_INTERVAL_MS,
        )
        metrics.set_meter_provider(
            MeterProvider(
                resource=Resource.create({SERVICE_NAME: settings.otel_service_name}),
                metric_readers=[reader],
            )
        )
    metrics.get_meter("sbam_api.bulkhead").create_observable_gauge(
        "sbam_bulkhead_saturated",
        callbacks=[observe],
        description="1 while every slot of a dependency's bulkhead is in use",
    )
