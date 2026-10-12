# 0008 — Knowledge: Cognee and OKF

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-12, C-13, [TD §5](../design/05-component-architecture.md)

## Context

Parsed documents become project knowledge that the SOW and the later stages draw on.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-12 | Cognee | Default Cognee stores; isolated per project; graph viewable/explorable, not editable | QA E1, E3, E4 |
| C-13 | OKF | OKF Bundle must preserve all ingested information (content + entities + relations + provenance) so the LLM has full context for the SOW | QA E2 |

## Consequences

- Cognee runs with its default stores, isolated per project; [ADR 0001](0001-cognee-okf-config.md) records the spike's configuration.
- The OKF Bundle must preserve all ingested information, so knowledge coverage is checked against every parsed document.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §5](../design/05-component-architecture.md)
