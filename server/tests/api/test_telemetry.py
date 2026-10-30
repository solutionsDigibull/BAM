"""E1.4 OpenTelemetry baseline, in-process: spans go to an in-memory exporter (no collector)."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sbam_api.core.logging import configure_logging
from sbam_api.core.settings import SettingsError
from sbam_api.core.telemetry import set_request_context, tracer_provider
from test_core_app import ALL_OK, log_lines, make_settings

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
INCOMING = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.secret-token-payload.sig"  # noqa: S105 - fake, must never be exported


class _Upstream(BaseHTTPRequestHandler):
    """Records the headers of each request it receives (stands in for an external API)."""

    seen: ClassVar[list[dict[str, str]]] = []

    def do_GET(self) -> None:
        type(self).seen.append({k.lower(): v for k, v in self.headers.items()})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="module")
def upstream() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def exporter(tmp_path_factory: pytest.TempPathFactory) -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    tracer_provider(make_settings(tmp_path_factory.mktemp("otel"))).add_span_processor(
        SimpleSpanProcessor(exp)
    )
    return exp


@pytest.fixture
def client(tmp_path: Path, exporter: InMemorySpanExporter, upstream: str) -> Iterator[TestClient]:
    from sbam_api.main import create_app

    app: FastAPI = create_app(make_settings(tmp_path), ALL_OK)
    engine = sa.create_engine("sqlite://")

    @app.get("/sample")
    def sample(authenticated: bool = False) -> dict[str, int]:
        # Stand-in for a real route: one DB query with a bound value, one outgoing call.
        if authenticated:  # what auth (#48) does after verifying the token
            set_request_context(tenant_id="tenant-a", project_id="p-1")
        with engine.connect() as conn:
            n = conn.execute(sa.text("SELECT length(:password)"), {"password": "secret"}).scalar()
        httpx.get(f"{upstream}/x", timeout=5)
        import logging

        logging.getLogger("sbam_api.test").info("inside sample")
        return {"n": int(n or 0)}

    exporter.clear()
    _Upstream.seen.clear()
    yield TestClient(app)


def spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return list(exporter.get_finished_spans())


def sql_statements(exporter: InMemorySpanExporter) -> list[str]:
    """db.statement (or the newer db.query.text) of every SQL span."""
    out = []
    for s in spans(exporter):
        attrs = s.attributes or {}
        text = attrs.get("db.statement") or attrs.get("db.query.text")
        if text:
            out.append(str(text))
    return out


def server_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    return next(s for s in spans(exporter) if s.kind == SpanKind.SERVER)


def test_e1_4_t01_route_with_db_and_httpx_gives_one_trace_with_context(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    res = client.get("/sample?authenticated=true", headers={"x-correlation-id": "corr-t01"})
    assert res.status_code == 200
    got = spans(exporter)
    assert len(got) >= 3
    assert len({s.context.trace_id for s in got}) == 1, "all spans in one trace"
    servers = [s for s in got if s.kind == SpanKind.SERVER]
    assert len(servers) == 1, "one server span: FastAPI's built-in telemetry must stay off"
    kinds = {s.kind for s in got}
    assert {SpanKind.SERVER, SpanKind.CLIENT} <= kinds  # route + db/httpx clients
    assert sql_statements(exporter), "no SQL span"
    attrs = server_span(exporter).attributes or {}
    assert attrs.get("sbam.correlation_id") == "corr-t01"
    assert attrs.get("sbam.tenant_id") == "tenant-a"
    assert attrs.get("sbam.project_id") == "p-1"


def test_e1_4_t02_incoming_traceparent_is_continued_and_propagated(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    client.get("/sample", headers={"traceparent": INCOMING})
    assert format(server_span(exporter).context.trace_id, "032x") == TRACE_ID
    outgoing = _Upstream.seen[-1]["traceparent"]
    assert outgoing.split("-")[1] == TRACE_ID


def test_e1_4_t03_no_token_or_sql_value_in_exported_spans(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    client.get(
        "/sample",
        headers={"authorization": f"Bearer {TOKEN}", "cookie": f"authjs.session-token={TOKEN}"},
    )
    dumped = "\n".join(s.to_json() for s in spans(exporter))
    assert TOKEN not in dumped
    assert "secret" not in dumped.replace("secret-token", "")  # the SQL bound value
    # The statement is kept with its placeholder, never the bound value.
    assert sql_statements(exporter) == ["SELECT length(?)"]


def test_e1_4_t05_malformed_traceparent_starts_a_new_trace(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    res = client.get("/sample", headers={"traceparent": "garbage"})
    assert res.status_code == 200
    span = server_span(exporter)
    assert span.context.trace_id != 0
    assert span.parent is None or not span.parent.is_remote


def test_e1_4_t06_log_line_carries_the_span_trace_id(
    capsys: pytest.CaptureFixture[str], client: TestClient, exporter: InMemorySpanExporter
) -> None:
    configure_logging()  # rebind the JSON handler to capsys's stdout (the fixture built the app)
    client.get("/sample")
    line = next(r for r in log_lines(capsys.readouterr().out) if r["msg"] == "inside sample")
    assert line["trace_id"] == format(server_span(exporter).context.trace_id, "032x")
    assert len(str(line["span_id"])) == 16


def test_e1_4_t07_tenant_header_never_becomes_a_span_attribute(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    client.get("/sample", headers={"x-tenant-id": "evil-tenant", "x-project-id": "evil"})
    dumped = "\n".join(s.to_json() for s in spans(exporter))
    assert "evil" not in dumped
    assert "sbam.tenant_id" not in json.loads(server_span(exporter).to_json())["attributes"]


def test_e1_4_exporter_endpoint_must_be_a_valid_url(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        make_settings(tmp_path, otel_exporter_otlp_endpoint="not a url")


def test_e1_4_fastapi_builtin_telemetry_stays_off_with_an_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentelemetry import metrics
    from sbam_api.main import create_app

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    with TestClient(create_app(make_settings(tmp_path), ALL_OK)) as c:  # runs the lifespan
        assert c.get("/health/live").status_code == 200
    # FastAPI auto-configure would have installed an SDK MeterProvider with an OTLP reader.
    assert type(metrics.get_meter_provider()).__name__ != "MeterProvider"


def test_e1_4_full_queue_drops_spans_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ending a span never waits on the exporter. The exporter here blocks until released, so a
    queue that made `end()` wait would hang the thread: the check is that it finishes, not how fast
    a loaded runner is (a wall-clock bound made this test fail on slow CI machines)."""
    import threading

    from sbam_api.core import telemetry

    monkeypatch.setattr(telemetry, "_provider", None)  # a fresh provider with a real exporter
    dead = make_settings(tmp_path, otel_exporter_otlp_endpoint="http://127.0.0.1:1")
    provider = tracer_provider(dead)
    release, finished = threading.Event(), threading.Event()

    def stuck_export(*_: object) -> SpanExportResult:
        release.wait(60)
        return SpanExportResult.FAILURE

    def emit() -> None:
        tracer = provider.get_tracer("e1_4")
        for i in range(5000):  # > max_queue_size (2048): the overflow is dropped, not waited on
            tracer.start_span(f"s{i}").end()
        finished.set()

    try:
        monkeypatch.setattr(OTLPSpanExporter, "export", stuck_export)
        threading.Thread(target=emit, daemon=True).start()
        assert finished.wait(30), "ending spans blocked on a full export queue"
    finally:
        release.set()
        # Shutdown flushes the queue; the exporter no longer blocks.
        monkeypatch.setattr(OTLPSpanExporter, "export", lambda *_: SpanExportResult.FAILURE)
        provider.shutdown()


def test_e1_4_background_work_without_request_context(
    capsys: pytest.CaptureFixture[str], exporter: InMemorySpanExporter
) -> None:
    import logging

    from sbam_api.core.logging import tenant_id_var

    configure_logging()
    token = tenant_id_var.set(None)
    try:
        set_request_context(tenant_id="tenant-a")  # no current span: no-op on tracing, no raise
        logging.getLogger("sbam_api.test").info("background job")
    finally:
        tenant_id_var.reset(token)
    line = next(r for r in log_lines(capsys.readouterr().out) if r["msg"] == "background job")
    assert "trace_id" not in line and "span_id" not in line


def test_e1_4_no_endpoint_means_no_exporter_and_no_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(tmp_path)
    assert settings.otel_exporter_otlp_endpoint is None
    from sbam_api.main import create_app

    res = TestClient(create_app(settings, ALL_OK)).get("/health/live")
    assert res.status_code == 200
    noisy = [r for r in log_lines(capsys.readouterr().out) if r["level"] in ("WARNING", "ERROR")]
    assert not noisy
