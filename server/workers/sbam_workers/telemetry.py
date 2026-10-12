"""Worker tracing (TD §17). The Temporal `TracingInterceptor` (sbam_api.temporal.client) reads
the W3C `traceparent` the API put in the workflow headers, so activity spans join the API request's
trace (E1.7-T08). Spans are exported over OTLP/HTTP only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from sbam_workers.settings import WorkerSettings


def tracer(settings: WorkerSettings) -> trace.Tracer:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.otel_service_name}))
    if settings.otel_exporter_otlp_endpoint:
        endpoint = str(settings.otel_exporter_otlp_endpoint).rstrip("/")
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
