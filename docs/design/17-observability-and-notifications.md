> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §17. Observability and notifications


## 17.1 Correlation

Every request, Temporal workflow, activity, LLM call and log line carries `correlation_id`, `tenant_id`, `project_id`, `workflow_id` (SBAM) and, where relevant, `sub_skill_id` and the Temporal ids. Context propagates over W3C `traceparent` through API → Temporal (OTEL interceptor) → workers → Bifrost.

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
