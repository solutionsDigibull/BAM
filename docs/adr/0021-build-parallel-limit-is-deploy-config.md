# 0021 — The build parallel limit is deploy configuration, not an admin API

- Status: Accepted
- Date: 2026-10-30
- Related: issues #354 (E10.3b), #108 (E10.3), PR #282; [TD §13.2](../design/13-temporal-design.md)

## Context

#108 (E10.3) limits how many Sub-Skill builds run at once. Its spec says "Tenant/platform config
`build.max_parallel`, int 1 to 16, default 4", and its test case `E10.3-T05` expects an invalid
value to be rejected with 422. PR #282 built the limit as platform config: `BuildSettings` reads
`BUILD_MAX_PARALLEL` from the worker's environment (default 4, `MAX_PARALLEL_BUILDS`), and a value
outside 1 to 16 is refused (`MAX_PARALLEL_LIMIT`). There is no config API, so a 422 was never
possible, and the test checks the Pydantic validation instead (`test_e10_3_t05_invalid_max_parallel_rejected`).
#354 asks whether the limit should get an admin endpoint (then 422 is real, with a test) or stay
deploy config (then the spec has to say so).

## Decision

- **The limit stays deploy configuration.** `BUILD_MAX_PARALLEL` in the worker's environment is the
  only way to change it; there is no admin endpoint for it. It is one value for the platform.
- **A bad value is refused where it is read, not at an HTTP boundary.** `BuildSettings` raises a
  validation error for a value outside 1 to 16, which fails the build plan activity rather than
  running with a silent default. `E10.3-T05` is met by that unit test; its "422" is read as "refused
  with a validation error", since there is no request to answer.
- **No per-tenant override.** The limit protects the shared sandbox and worker capacity, which
  belong to the platform and not to a tenant.

## Consequences

- Changing the limit means redeploying the worker with the new environment value, and it applies to
  the next plan that is made. That suits a capacity setting that operations own.
- `docs/design/13-temporal-design.md` and #108's "Parallel limit" row keep saying
  `build.max_parallel`; this ADR is the note that it is the environment setting, not an API.
- If a tenant ever needs its own limit, that is a new story with an endpoint, the same 1 to 16
  validation, a 422 test and an audit row; this ADR is then superseded.
- The other gaps PR #282 listed (counts not mirrored to `temporal_runs`, a dependency cycle failing
  inside the workflow instead of a 409 at generate) are not decided here.
