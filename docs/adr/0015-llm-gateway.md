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

## Cost control (added 2026-10-28)

C-41 stays: there is **no budget limit by default**. Two additions reduce spend without limiting it:

- **Derivative cache** (`llm_derivatives`, migration 0035). The worker's LLM client keys every call by
  the SHA-256 of the alias and the exact messages (prompt text and version, output schema, every
  document and image). A repeat of identical input is answered from the stored, re-validated answer
  and costs nothing, in any project of the same tenant; it never crosses tenants. Changing the prompt,
  the schema or any document is a different key, so a stale answer cannot be served. A hit writes a
  zero-cost `llm_calls` row (`model = derivative-cache`). `LLM_DERIVATIVE_CACHE=false` turns it off.
- **Opt-in budgets.** `LLM_PROJECT_BUDGET_USD` and `LLM_TENANT_BUDGET_USD` (unset = unlimited) refuse
  the next model call with the non-retryable `BudgetExceeded` once recorded spend (`llm_calls.cost_usd`,
  failed attempts included) reaches them. Cache hits are still served.

Not covered: Cognee's own LLM calls (`cognify` goes through Bifrost, not this client), so they are
neither cached nor counted by the worker-side budget. Cached answers are not purged with `llm_calls`
yet; add them to retention when that policy is written.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §5](../design/05-component-architecture.md)
