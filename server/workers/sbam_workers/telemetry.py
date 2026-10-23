"""Worker tracing and metrics (TD §17). The Temporal `TracingInterceptor` (sbam_api.temporal.client)
reads the W3C `traceparent` the API put in the workflow headers, so activity spans join the API
request's trace (E1.7-T08). Spans and metrics (e.g. posthog_events_*, llm_trace_export_failures
(E16.2)) are exported over OTLP/HTTP only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
"""

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from sbam_workers.settings import WorkerSettings


def tracer(settings: WorkerSettings) -> trace.Tracer:
    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        endpoint = str(settings.otel_exporter_otlp_endpoint).rstrip("/")
        # Counters made at import time (the meters are proxies) start exporting from here.
        metrics.set_meter_provider(
            MeterProvider(
                resource=resource,
                metric_readers=[
                    PeriodicExportingMetricReader(
                        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=2),
                        export_interval_millis=30_000,
                    )
                ],
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(  # bounded queue: a dead collector drops spans, never blocks work
                OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=2),
                max_queue_size=2048,
                export_timeout_millis=2000,
            )
        )
    trace.set_tracer_provider(provider)
    set_global_textmap(TraceContextTextMapPropagator())  # W3C only, no baggage (same as the API)
    return provider.get_tracer("sbam_workers")
