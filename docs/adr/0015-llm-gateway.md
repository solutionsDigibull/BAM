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
  **Retention (#497):** the key is a hash, so a purged document's rows cannot be found by document. Instead
  a daily schedule (`SweepLlmDerivatives`, 05:00 UTC, after the derived-file sweep) hard-deletes rows
  unused for `LLM_DERIVATIVE_TTL_DAYS` (default 90, the document bin's retention; 0 keeps them); use is
  `last_hit_at`, else `created_at`, and a hit refreshes it. Exact deletion by document (reference counts
  per source version) was not chosen: a row built from several documents needs counting, and a bounded
  lifetime is enough until policy asks for more. One audit row of counts per run that deleted anything.
- **Opt-in budgets.** `LLM_PROJECT_BUDGET_USD` and `LLM_TENANT_BUDGET_USD` (unset = unlimited) refuse
  the next model call with the non-retryable `BudgetExceeded` once recorded spend (`llm_calls.cost_usd`,
  failed attempts included) reaches them. Cache hits are still served.

### Hardening (#420, added 2026-10-30)

- **The limit must be a real number.** `LLM_PROJECT_BUDGET_USD` / `LLM_TENANT_BUDGET_USD` refuse NaN,
  Infinity, zero and negatives at worker start (NaN would make every comparison false, so the budget
  would never trip; Infinity is no limit). An empty value, which Compose passes when unset, means no
  limit.
- **A budget needs a cost to count.** The cost is Bifrost's own figure; when it reports none (a model
  its catalogue cannot price) the price in `LLM_PRICE_USD_PER_MTOK` for the alias stands in (JSON, USD per
  million tokens, `{"alias": {"in": x, "out": y}}`). A call that used tokens and has neither is not
  counted as free: its alias is refused from the next call on (`BudgetExceeded`, with the reason), once
  per process it logs an error, and an operator either fixes Bifrost's pricing, adds the price, or declares
  a free model with `{"in": 0, "out": 0}`. The first such call is not wasted: its answer is stored and
  returned. Rejected: refusing to start, because whether Bifrost reports a cost is only known after a
  call.
- **The budget is a soft limit.** It is checked before a call against *recorded* spend, so calls running
  at the same time can overshoot it by what is in flight: at most (parallel calls) x (cost of one call).
  A reservation made before the call (#419) is what makes it a hard limit.
- **Cognee is metered, not refused.** `cognify` and the embeddings it needs run inside worker-knowledge
  and reach Bifrost without passing the wrapper. They go through LiteLLM, whose success callback
  (`llm/meter.py`) now writes one `llm_calls` row per call (`prompt_id = cognee.<call type>`, aliases
  `sbam-knowledge` / `sbam-embed`) for the tenant and project the Cognee adapter named around the call.
  The spend therefore counts toward the budgets and the wrapper's next call is refused when it is
  reached. A `cognify` that has started is not stopped, so it can overshoot by one run. Cognee has no
  cached answers: its calls are not de-duplicated. Rejected: routing Cognee through `LlmClient` (its
  calls are made by Cognee's own pipeline, not ours) and polling Bifrost's logs (no tenant or project
  on them).
- **Real PostgreSQL coverage.** The migration (`0037`, down and up), the JSONB column and the unique-key
  race of `put` now have tests on PostgreSQL (`tests/unit/test_llm_derivatives_postgres.py`; they run in
  CI's unit job, which has a PostgreSQL service, and fail the job if skipped).
- **Wiring.** `LLM_DERIVATIVE_CACHE`, the two budgets and the price table are passed to the workers by
  `compose/compose.yaml` (before this, setting them in the environment did nothing) and documented in
  `compose/.env.example`.

Not covered: cached answers are not purged with `llm_calls` yet (#497).

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §5](../design/05-component-architecture.md)
