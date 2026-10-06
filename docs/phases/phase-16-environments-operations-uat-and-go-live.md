> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 16 — Environments, Operations, UAT and Go-Live

| Epic | Milestone | Stories |
|---|---|---|
| [#17](https://github.com/solutionsDigibull/BAM/issues/17) `E16` | M5 Harden + Go-live | 6 |


**Goal:** Run SBAM reliably in dev, staging and production (Docker Compose on VMs), with observability, backups and UAT sign-off.

**Owner:** Dev A + all · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 15
**Design references:** [TD §17](../design/17-observability-and-notifications.md), [TD §20](../design/20-docker-and-environments.md).

### Environments and release

- [ ] Compose files per environment (dev, staging, prod) using the same image digests; promotion only by digest.
- [ ] Reverse proxy with TLS (Caddy/Traefik, *proposed*) as the only public entry; Netbird mesh for SSH, Temporal UI, Grafana, Langfuse and the CubeSandbox host.
- [ ] Migration job before rollout; rollback runbook (previous digests + forward-fix policy); graceful worker draining.
- [ ] GitHub Actions deploy workflows (manual approval for production).

### Observability

- [ ] OTEL collector → Grafana LGTM (*proposed*) dashboards: API, Temporal queues, gate pass rates, sandbox, LLM latency/cost.
- [ ] Langfuse projects per environment; PostHog events (funnel: workflow created → frozen; gate failures; corrections).
- [ ] In-app alerts for failures and pending actions (C-43); operator alerts in Grafana.

### Backup and recovery

- [ ] Nightly PostgreSQL dumps (all databases) + storage volume snapshot + Cognee volume snapshot; retention 30 days *(proposed)*.
- [ ] Restore drill:

```text
Failure → Restore PostgreSQL → Restore storage + Cognee volumes → Start Temporal + workers
        → Re-verify hashes of frozen manifests → Smoke tests → Measure RPO/RTO
```

### UAT

- [ ] Admin UAT (tenant, project, masters, RBAC) and FDE UAT (full flow to export) on staging with real (non-sensitive) project data.
- [ ] Runbooks: stuck Temporal workflow, CubeSandbox host down, OpenRouter outage, ClamAV signature failure, disk full.

### Exit Criteria

- [ ] Restore drill succeeds within RPO 24 h / RTO 8 h, and all frozen manifests re-verify.
- [ ] Admin and FDE UAT are signed off with no open critical/high defects.
- [ ] The first real Workflow is certified, frozen and exported in production.

## Design pages to read

- [TD §17. Observability and notifications](../design/17-observability-and-notifications.md)
- [TD §20. Docker and environments](../design/20-docker-and-environments.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#140](https://github.com/solutionsDigibull/BAM/issues/140) | `E16.1` | Grafana LGTM dashboards + operator alerts | A Platform | M | P0 | M4 | [#42](https://github.com/solutionsDigibull/BAM/issues/42) |
| [#141](https://github.com/solutionsDigibull/BAM/issues/141) | `E16.2` | Langfuse projects + PostHog funnel/eval events | A Platform | S | P0 | M4 | [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#142](https://github.com/solutionsDigibull/BAM/issues/142) | `E16.3` | dev/staging/prod Compose + reverse proxy + Netbird policies | A Platform | M | P0 | M4 | [#43](https://github.com/solutionsDigibull/BAM/issues/43) |
| [#143](https://github.com/solutionsDigibull/BAM/issues/143) | `E16.4` | Deploy workflows + rollback | A Platform | S | P0 | M5 | [#142](https://github.com/solutionsDigibull/BAM/issues/142) |
| [#144](https://github.com/solutionsDigibull/BAM/issues/144) | `E16.5` | Backups + restore drill | A Platform | S | P1 | M5 | [#142](https://github.com/solutionsDigibull/BAM/issues/142) |
| [#145](https://github.com/solutionsDigibull/BAM/issues/145) | `E16.6` | Runbooks, UAT scripts and readiness review | A Platform | M | P0 | M5 | [#136](https://github.com/solutionsDigibull/BAM/issues/136), [#143](https://github.com/solutionsDigibull/BAM/issues/143) |

---
[← Phase 15](phase-15-end-to-end-testing-security-and.md)   [Phases index](README.md)
