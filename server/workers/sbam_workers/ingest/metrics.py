"""Scan metrics (E4.3, TD §17): `clamav_signature_age_seconds` and `scan_duration_seconds`.

Exported over OTLP only while OTEL_EXPORTER_OTLP_ENDPOINT is set (the `obs` profile); otherwise the
instruments are no-ops. The age gauge asks clamd for its signature date every export interval, so it
keeps moving when nobody uploads anything and the "older than 48 h" alert still fires; between
probes (or when clamd cannot be reached) it reports the age seen by the last scan.

It lives in the ingest module so the worker bootstrap is not edited; `start` is called from the
ingest worker only.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import datetime

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Histogram, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from sbam_workers.ingest.clamd import ClamVersion, signature_age
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.metrics")

EXPORT_INTERVAL_MS = 15_000
_last_age: float | None = None
_probe: Callable[[], float | None] | None = None
_started = False
_histogram: Histogram | None = None


def observe_signature_age(version: ClamVersion, now: datetime) -> None:
    """Remember the age a scan just saw."""
    global _last_age
    age = signature_age(version, now)
    if age is not None:
        _last_age = age.total_seconds()


def observe_scan(seconds: float, *, infected: bool) -> None:
    global _histogram
    if _histogram is None:
        _histogram = metrics.get_meter("sbam_workers.ingest").create_histogram(
            "scan_duration", unit="s", description="Time clamd took to scan one document"
        )
    _histogram.record(seconds, {"verdict": "infected" if infected else "clean"})


def _observe_age(options: CallbackOptions) -> Iterable[Observation]:
    age: float | None = None
    if _probe is not None:
        try:
            age = _probe()
        except Exception as exc:  # clamd unreachable: fall back to the last known age
            log.info("signature age probe failed", extra={"exc_type": type(exc).__name__})
    age = age if age is not None else _last_age
    if age is not None:
        yield Observation(age)


def start(probe: Callable[[], float | None], settings: WorkerSettings) -> None:
    """Register the age gauge (once per process). `probe` gives clamd's signature age in seconds."""
    global _probe, _started
    if _started:
        return
    _started = True
    _probe = probe
    # The worker's telemetry may already export metrics (E16.2); a second provider is refused.
    if settings.otel_exporter_otlp_endpoint and not isinstance(
        metrics.get_meter_provider(), MeterProvider
    ):
        endpoint = str(settings.otel_exporter_otlp_endpoint).rstrip("/")
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=2),
            export_interval_millis=EXPORT_INTERVAL_MS,
        )
        provider = MeterProvider(
            resource=Resource.create({SERVICE_NAME: settings.otel_service_name}),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
    metrics.get_meter("sbam_workers.ingest").create_observable_gauge(
        "clamav_signature_age_seconds",
        callbacks=[_observe_age],
        unit="s",
        description="Age of clamd's signature database",
    )
