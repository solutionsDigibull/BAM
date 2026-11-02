# 0024 — Rubric grading runs in the sbam-promptfoo sidecar, reached through a job directory, and only through Bifrost

- Status: Accepted
- Date: 2026-11-02
- Related: issues #638 (E13.6c), #127 (E13.6), refines [0012](0012-testing-sandbox-and-certification.md) and [0023](0023-sandbox-adapter.md), [TD §15.1](../design/15-test-data-promptfoo-and-sandbox-design.md)

## Context

A suite may carry an `llm-rubric` assertion for a free-text output. The deterministic assertions run in the worker
(decision on #127); the rubric needs a model. Promptfoo grades it, but the `sbam-promptfoo` image is node-only
and nothing ran it. The rule "LLM calls go only through Bifrost with an alias" holds for this call too.

## Decision

1. **A sidecar with a job directory**, the pattern of `sbam-toolchain` (E12.4b): the worker writes
   `request.json` and `READY` (last) into a shared volume, the container writes `result.json` then `DONE`. No
   socket, no Docker socket. Read-only root, tmpfs scratch, uid 10001, no capabilities, resource limits.
2. **Only through Bifrost, with one alias.** The container sits on the internal network (no internet route), is
   given `BIFROST_URL` and no provider key, and may ask for `sbam-rubric` only (the runner refuses any other alias).
   `sbam-rubric` is a normal alias in `aliases.json` (real and mock configs generated from it). The mock answers
   with Promptfoo's grading JSON and fails a request that carries `MOCK_RUBRIC_FAIL`.
3. **The request is data, not configuration.** The runner builds the Promptfoo config from a fixed template: the
   `echo` provider returns the output under grade, which goes in as a variable; case names are checked against a
   pattern; sizes are capped. Nothing from the request can choose a provider, a command or a file.
4. **Only a met or an unmet rubric is a verdict.** Measured against Promptfoo 0.124.0: with Bifrost down it still
   writes the row as `success: false`, `failureReason: 1`, with an "API call error: ..." reason (exit 100), exactly
   like an unmet rubric. The runner reads that prefix as the infrastructure's failure and answers with an `error`,
   never a verdict. The worker's `SidecarGrader` turns any error, timeout, missing directory, missing or
   non-boolean verdict into `PromptfooInfraError`; `grade_with_retries` asks three times and then testing is
   blocked (TESTING_BLOCKED), with no correction spent (E13.6-T07).
5. **No grader configured blocks testing.** `PROMPTFOO_JOBS_DIR` empty means no grader: a suite with a rubric case
   blocks testing instead of being scored, so nothing is ever read as the code's fault because the platform could not grade it.

## Consequences

- A rubric case needs the `promptfoo` service; `worker-test` depends on it and mounts the same volume.
- The grading model is a Bifrost routing choice (`sbam-rubric`), changed without a code change.
- The failure-reason heuristic depends on Promptfoo's wording ("API call error"); a Promptfoo upgrade must re-run
  `test_e13_6c_*` and the outage check described in #638.
