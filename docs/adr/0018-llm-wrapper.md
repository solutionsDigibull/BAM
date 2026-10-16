# 0018 — LLM wrapper: plain httpx, Langfuse over REST, replay cache in `llm_calls`

- Status: Accepted
- Date: 2026-10-15
- Related: issue #85 (E6.1), refines [0015](0015-llm-gateway.md), [TD §5.4](../design/05-component-architecture.md)

## Context

E6.1 adds the one wrapper every worker uses to call a model (`llm.run(alias, prompt_id, output_type,
context, scope)`). Three choices were open: a Pydantic AI dependency, the Langfuse SDK, and where the
replay cache lives.

## Decision

- **No Pydantic AI.** Bifrost already speaks the OpenAI chat API, and the bounded re-ask loop
  (`sbam_workers/llm/reask.py`, E1.15) is the part of it the plan needs. The wrapper calls Bifrost with
  `httpx` and keeps no extra dependency.
- **Langfuse over its REST ingestion API** (`/api/public/ingestion`) with `httpx`, not the SDK. No new
  dependency, and a Langfuse that is down costs a warning and the `llm_trace_export_failures_total`
  counter, never a failed call. Prompts and answers are exported as size and hash unless
  `LANGFUSE_LOG_CONTENT=true` (development).
- **The replay cache is the `llm_calls` row.** A SUCCEEDED row holds the validated output (JSONB). An
  activity replayed with the same (Temporal workflow id, activity id, attempt) reads it instead of
  calling Bifrost. A new Temporal attempt is a new call and a new bill.
- **Output types must set `extra="forbid"`.** A model that sends `status`, `is_locked` or any other
  platform field is re-asked and the field is never persisted.

## Consequences

- Revisit the SDK if tracing needs more than generations and tags (scores, datasets).
- The worker env gains `BIFROST_URL`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `LANGFUSE_LOG_CONTENT` and `LLM_*` limits. All have safe defaults; Compose passes them with the first
  stage that calls the wrapper.
