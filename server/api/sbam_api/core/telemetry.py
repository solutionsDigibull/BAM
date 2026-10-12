"""OpenTelemetry tracing for sbam-api (E1.4, TD §17).

Spans for FastAPI routes, SQLAlchemy queries and outgoing httpx calls, exported over OTLP/HTTP only
when OTEL_EXPORTER_OTLP_ENDPOINT is set (the Compose `obs` profile sets it). Safety rules:
- Telemetry never blocks requests: a bounded batch queue drops spans if the collector is down.
- SQL spans keep the statement with its placeholders; bound values are never recorded.
- Authorization/Cookie are never captured, even if header capture is switched on by env.
- tenant/project/workflow attributes come only from `set_request_context`, which auth calls after
  authenticating (#48), never from request headers.
"""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from sbam_api.core.logging import project_id_var, tenant_id_var
from sbam_api.core.settings import Settings

SECRET_HEADERS = ["authorization", "cookie", "set-cookie", "proxy-authorization"]
# ponytail: one provider per process; create_app may run many times (tests) and reuses it.
_provider: TracerProvider | None = None


def tracer_provider(settings: Settings) -> TracerProvider:
    """The process-wide provider, created (with its exporter, if configured) on first use."""
    global _provider
    if _provider is None:
        _provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
        )
        if settings.otel_exporter_otlp_endpoint:
            endpoint = str(settings.otel_exporter_otlp_endpoint).rstrip("/")
            _provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=2),
                    max_queue_size=2048,  # full queue = dropped spans, never a blocked request
                    export_timeout_millis=2000,
                )
            )
        trace.set_tracer_provider(_provider)
        # W3C trace context only: no baggage, so headers can't smuggle in attributes.
        set_global_textmap(TraceContextTextMapPropagator())
    return _provider


def configure_telemetry(app: FastAPI, settings: Settings) -> None:
    provider = tracer_provider(settings)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        http_capture_headers_sanitize_fields=SECRET_HEADERS,
        exclude_spans=["receive", "send"],  # per-message ASGI spans are noise
    )
    httpx_instrumentor = HTTPXClientInstrumentor()
    if not httpx_instrumentor.is_instrumented_by_opentelemetry:
        httpx_instrumentor.instrument(tracer_provider=provider)
    sql_instrumentor = SQLAlchemyInstrumentor()
    if not sql_instrumentor.is_instrumented_by_opentelemetry:
        sql_instrumentor.instrument(tracer_provider=provider, enable_commenter=False)


def set_request_context(
    *, tenant_id: str | None = None, project_id: str | None = None, workflow_id: str | None = None
) -> None:
    """Record the authenticated request's scope on the log context and the current span.

    Call only after authentication (#48): these values are trusted; header values never are.
    """
    span = trace.get_current_span()
    if tenant_id is not None:
        tenant_id_var.set(tenant_id)
        span.set_attribute("sbam.tenant_id", tenant_id)
    if project_id is not None:
        project_id_var.set(project_id)
        span.set_attribute("sbam.project_id", project_id)
    if workflow_id is not None:
        span.set_attribute("sbam.workflow_id", workflow_id)
