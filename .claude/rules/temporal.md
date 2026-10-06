---
paths:
  - "server/workers/**"
  - "server/api/sbam_api/temporal/**"
---

# Temporal rules (workers)

- Workflow code (`pipeline/`: `ProjectPipeline`, `SubSkillBuild`) must be deterministic:
  - no I/O, network, DB or file access;
  - no `datetime.now()`, `random` or `uuid4()`; use `workflow.now()` and `workflow.uuid4()`;
  - no threads.
- Everything with side effects is an activity in its module (`ingest/`, `knowledge/`, `sow/`, `isatvon/`,
  `build/`, `test/`, `certify/`, `exports/`). Activities register themselves with the registry decorator.
  Never edit `bootstrap.py` to add one.
- Activity inputs and outputs are Pydantic models from `server/contracts`. Pass IDs and storage keys, not
  large payloads.
- Activities are idempotent: a retry must not duplicate rows, files or LLM charges. Use the run ID plus the
  step as the idempotency key.
- Set explicit timeouts and retry policies. Non-retryable domain errors (validation, `NEEDS_FDE_REVIEW`)
  raise `ApplicationError(non_retryable=True)`.
- Signals: `sow_approved`, `generate`, `retry`, `rebuild_subskills`, `cancel`. Search attributes:
  `TenantId`, `ProjectId`, `SbamWorkflowId`, `SkillId`, `SbamStage`.
- Changing workflow logic that may already be running needs `workflow.patched("<change-id>")`. Add a replay
  test from a recorded history in `server/tests/temporal`, which CI runs.
- The correction loop runs at most 2 attempts, then sets `NEEDS_FDE_REVIEW`. Sandbox infrastructure
  failures go to `TESTING_BLOCKED` (retry), never into the correction loop.
