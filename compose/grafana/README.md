# Grafana dashboards and operator alerts (E16.1, TD §17.3)

Provisioned as code into the `obs` profile's `otel-collector` service (`grafana/otel-lgtm`). Nothing here is
edited in the Grafana UI: dashboards are read-only there, and alert rules are provisioned (change them in Git).

| Path | What |
|---|---|
| `dashboards/*.json` | Six dashboards in folder SBAM: API health, Temporal queues, Pipeline/gates/corrections, Sandbox, LLM latency and cost, Platform (ClamAV age, disk, frozen-hash integrity) |
| `provisioning/dashboards/sbam.yaml` | Dashboard provider |
| `provisioning/alerting/rules.yaml` | The seven TD §17.3 operator alerts, plus "frozen-hash job stale" and "telemetry missing" |
| `provisioning/alerting/contact-points.yaml` | Operator channel: one webhook, URL from `GRAFANA_ALERT_WEBHOOK_URL` |
| `provisioning/alerting/policies.yaml` | Routing and flap damping (`critical` repeats hourly, the rest every 4 h) |
| `metrics-catalog.yaml` | Every metric the dashboards and alerts use, and whether the code emits it yet |

## Metrics: what is real today

Only the span-metrics Tempo derives from `sbam-api`'s traces exist today (API health dashboard, 5xx alert,
missing-telemetry alert). Everything else is `planned` in `metrics-catalog.yaml`, with the owning issue: Temporal
SDK metrics (workers need a Temporal `Runtime` with an OTLP metrics exporter), sandbox/gate/LLM/ClamAV/frozen-hash
metrics (emitted by their own modules), and filesystem usage (hostmetrics on each VM). Those panels stay empty and
those rules stay quiet (or fire on "no data", see below) until the owner lands. When a metric starts being emitted,
flip it to `emitted` with `evidence`.

`noDataState: Alerting` is set where silence is the failure: sandbox host (no runs means no error rate), ClamAV
age, frozen-hash verification. On a stack where those emitters don't exist yet, they therefore fire. Locally that
goes to the discard webhook.

## Access

Grafana publishes only on `127.0.0.1:${GRAFANA_PORT}` and anonymous access is off (the image default is anonymous
Admin). Off-box access is over Netbird only (TD §20.5, wired by #142); never publish this port through the public proxy.

## Changing things

- Dashboard: edit the JSON; every query must use catalog metrics and labels. Use `$$` for a literal `$` in alert
  files (Grafana expands `${VAR}` there); dashboard JSON is not expanded.
- Alert: add a rule to `rules.yaml` with a `for` period, a `severity` label and a runbook pointer.
- Validate without a stack: `cd server && uv run pytest tests/unit/test_grafana_provisioning.py`.
- With `make up PROFILES=obs`: `cd server && uv run pytest -m integration tests/integration/test_grafana_alerts.py`.
