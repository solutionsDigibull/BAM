# 0015 — LLM gateway

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-41, [TD §5](../design/05-component-architecture.md)

## Context

Several stages call LLMs. Model choice, cost and fallbacks need one control point.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-41 | LLM | OpenRouter through Bifrost; model chosen per task; external providers allowed; no budget limit | QA O1–O3 |

## Consequences

- Every LLM call goes through Bifrost with an `sbam-*` alias; no direct SDK calls. CI uses the mock provider.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §5](../design/05-component-architecture.md)
