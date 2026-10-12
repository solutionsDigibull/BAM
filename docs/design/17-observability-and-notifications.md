> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §17. Observability and notifications


## 17.1 Correlation

Every request, Temporal workflow, activity, LLM call and log line carries `correlation_id`, `tenant_id`, `project_id`, `workflow_id` (SBAM) and, where relevant, `sub_skill_id` and the Temporal ids. Context propagates over W3C `traceparent` through API → Temporal (OTEL interceptor) → workers → Bifrost.

### 17.1.1 sbam-api tracing baseline (E1.4)

`sbam_api/core/telemetry.py` traces FastAPI routes, SQLAlchemy queries and outgoing httpx calls.

| Setting | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP base URL (`/v1/traces` is appended). **Unset = no exporter, no errors.** The Compose `obs` profile sets it to `http://otel-collector:4318` for `sbam-api` only. An invalid URL fails startup. |
| `OTEL_SERVICE_NAME` | `service.name`, default `sbam-api` |

- **Attributes:**
  - `sbam.correlation_id` on every server span.
  - `sbam.tenant_id`, `sbam.project_id`, `sbam.workflow_id` only through `set_request_context(...)`. Auth calls it **after** authentication (#48); request headers never set them.
  - JSON log lines carry `trace_id`/`span_id` while a span is active.
- **Propagation:** W3C TraceContext only, with no baggage. A malformed `traceparent` starts a new trace. Outgoing httpx calls carry `traceparent`.
- **Safety:**
  - SQL spans keep the statement with its placeholders, never the bound values.
  - `Authorization`, `Cookie` and `Set-Cookie` are never captured, and are sanitized even if header capture is switched on by env.
  - The exporter sits behind a bounded batch queue (2048 spans, 2 s timeout), so a collector outage drops spans and never slows or fails requests.
- **Versions:** `opentelemetry-instrumentation-sqlalchemy` 0.65 supports SQLAlchemy `< 2.1`, so SQLAlchemy is pinned to 2.0.x until the instrumentation supports 2.1.

## 17.2 Signals

| Signal                   | Tool                                                                                          | Content                                                                                                                                             |
| ------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Traces, metrics, logs    | OpenTelemetry → collector → Grafana LGTM (Tempo, Prometheus/Mimir, Loki, Grafana)*(O-09)* | API latency/errors, queue latency, activity durations, gate pass rates, sandbox metrics, disk usage                                                 |
| LLM                      | Langfuse (self-hosted)                                                                        | Every call: alias, model, prompt version, input/output (redacted per policy), tokens, cost, latency, scores from golden sets                        |
| Product / eval analytics | PostHog (C-43)                                                                                | Funnel (Workflow created → SOW approved → Generate → Frozen), gate failures, corrections per Sub-Skill, time per stage, FDE edits on SOW/ISATVON |
| Operations               | Temporal UI                                                                                   | Workflow histories (operators only, via Netbird)                                                                                                    |

## 17.3 Dashboards and alerts

- **Dashboards:** API health, Temporal queues, pipeline throughput, gate pass rate per gate, correction rate, sandbox success rate/latency, LLM latency/cost by alias, ClamAV signature age, disk usage.
- **Operator alerts (Grafana):** 5xx > 2% / 5 min; queue schedule-to-start > 2 min; sandbox infra errors > 10% / 30 min; **sandbox host unreachable (health probe failing) > 2 min**; ClamAV signatures > 24 h old; disk > 80%; frozen-hash verification failure.
- **User alerts (in-app only, C-43):** SOW ready, breakdown ready, Sub-Skill needs review, Workflow certified/frozen, export ready, document rejected, generation failed.

---
[← §16](16-security-and-multi-tenancy.md)   [Design index](README.md)   [§18 →](18-error-handling-matrix.md)
