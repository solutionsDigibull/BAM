# 0016 — Audit, observability and NFRs

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-42, C-43, C-44, [TD §17](../design/17-observability-and-notifications.md)

## Context

The MVP needs to know what happened and how the system performs, without compliance-grade requirements yet.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-42 | Compliance / audit | None for now; normal audit table | QA P1, P2 |
| C-43 | Observability | OTEL + Langfuse + PostHog all in MVP; alerts only inside SBAM | QA Q1, Q2 |
| C-44 | NFRs | ≤10 concurrent users for MVP; proposed latency/availability/recovery targets accepted | QA R1, R2 |

## Consequences

- Audit is a normal append-only table (state changes go through the state-machine service).
- OTEL, Langfuse and PostHog are all in the MVP (#35, #42); the NFR targets size the stack for up to 10 concurrent users.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §17](../design/17-observability-and-notifications.md)
