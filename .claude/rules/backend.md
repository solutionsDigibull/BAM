---
paths:
  - "server/**"
---

# Backend rules (server/)

- Python 3.12 with uv. Ruff, Black and `mypy --strict` must pass. Use pytest.
- FastAPI modules follow `router → service → repository`. Routers hold no business logic. Each module in
  `server/api/sbam_api/<module>/` exposes `router`, which is auto-discovered.
- Every query goes through the tenant-scoped base repository. Cross-tenant, or a project the user isn't
  assigned to, returns **404** (no existence leak). Every new endpoint gets RBAC and IDOR tests.
- Validate every input with Pydantic v2 models from `server/contracts`. Never redefine a contract model locally.
- Errors are problem+json with a stable `code` (e.g. `SOW_STALE`), mapped to the failure matrix in TD §18.
  `If-Match` mismatch → 412, missing → 428. Frozen resource → 423.
- Start commands take an `Idempotency-Key`. Long operations return 202 with a run object and start
  Temporal work. A handler never parses, calls an LLM, generates code or calls the sandbox inline.
- Project files go only through `StorageBackend`. LLM calls go only through the Bifrost client with an
  `sbam-*` alias. Prompts live in the prompt registry, and every call is logged to `llm_calls` and Langfuse.
- State changes call the state-machine service, which writes the audit log. Don't set status columns directly.
- Logs and traces carry `tenant_id`, `project_id`, `workflow_id` and the correlation ID. Never log secrets,
  tokens or document contents.
- Tests:
  - Unit tests in `server/tests/unit`, API tests in `tests/api`, contract tests in `tests/contract`.
  - LLM tests use the mock provider and fixtures from `server/tests/fixtures`.
  - Name each test after its issue test-case ID, e.g. `test_e4_2_t02_oversize_rejected`.
