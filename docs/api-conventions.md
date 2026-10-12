> Docs: [Home](README.md) · [API design (TD §8)](design/08-api-design.md) · [Error matrix (TD §18)](design/18-error-handling-matrix.md)

# SBAM API conventions

Every SBAM endpoint follows these rules. The models are in
[`server/contracts/sbam_contracts/api.py`](../server/contracts/sbam_contracts/api.py). The helpers
that enforce them are in [`server/api/sbam_api/core/conventions.py`](../server/api/sbam_api/core/conventions.py).
Reviewers should reject an endpoint that doesn't follow them.

## Base and paths

- Base path: **`/api/v1`**.
- **Admin** APIs live under `/admin/...` and need the `sbam-admin` role (`super-admin` for tenants).
- **P** = `/projects/{project_id}`: project-level (uploads, documents, knowledge, SOW, pipeline). It needs
  `sbam-fde` **and** assignment to the project.
- **W** = `/projects/{project_id}/workflows/{workflow_id}`: the Workflow **SBAM generated** from the approved SOW.
- **There is no API for an FDE to create a Workflow.** W paths only read or send commands (generate,
  retry, cancel, export) to Workflows SBAM created. Never add a `POST P/workflows`.
- Auth uses `Authorization: Bearer <Keycloak access token>`. The tenant comes from the token and must
  match the resource.

## Errors: `application/problem+json`

Every 4xx/5xx response is a [`ProblemDetail`](../server/contracts/schemas/problem_detail.schema.json):

```json
{
  "type": "about:blank",
  "title": "Locked",
  "status": 423,
  "detail": "This resource is frozen / approved — create a new version to change it.",
  "code": "RESOURCE_LOCKED",
  "errors": [],
  "correlation_id": "01J9Z6Q8K4X2V7M3N5P0R1S2T3"
}
```

- **`code`** is a stable machine-readable `ErrorCode`. Clients branch on `code`, never on `title` or `detail`.
- **`errors[]`** lists field errors as `{path, message}`, for example `{"path": "limit", "message": "must be between 1 and 100"}`.
- **`correlation_id`** matches the `X-Correlation-ID` response header and the logs.
- **Never put stack traces, SQL, server paths, tokens or exception messages in `detail`.** The model
  rejects the obvious patterns, but the rule is: write `detail` for the user, never from `str(exc)`.
- Raise `ApiError(status, code, title, detail?, errors?)` in code. The exception handler (#34) renders it.
- Examples for each status are in [`server/contracts/examples/problems/`](../server/contracts/examples/problems/).

### Error-code registry

`ErrorCode` in `api.py` is the single registry, and it's exported to TypeScript by `make contracts` (#33).

- Add a code there in the PR that first returns it.
- Never rename, reuse or remove a code.
- Use the generic code (`VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`, …) unless the client must react differently.

## Status codes

| Status | When | Typical `code` |
|---|---|---|
| 200 / 201 / 204 | OK / created / no body | — |
| **202** | Long work started (parse, LLM, codegen, sandbox). The body is a `run` object; poll `W/runs/{id}` every 3–5 s | — |
| 400 | Malformed query or header (limit, sort, cursor, Idempotency-Key) | `VALIDATION_ERROR`, `INVALID_CURSOR` |
| 401 | No or invalid token | `UNAUTHENTICATED` |
| 403 | Wrong portal or role | `WRONG_PORTAL`, `FORBIDDEN` |
| **404** | Missing **or** another tenant's resource **or** a project you aren't assigned to | `NOT_FOUND` |
| 409 | State conflict (already running, not approved, …) | `PIPELINE_RUNNING`, `SOW_NOT_APPROVED`, … |
| 412 | `If-Match` doesn't match the current ETag | `PRECONDITION_FAILED` |
| 413 / 415 | File too large / unsupported type | `FILE_TOO_LARGE`, `UNSUPPORTED_TYPE` |
| 422 | Body fails validation; Idempotency-Key reused with a different body | `VALIDATION_ERROR`, `IDEMPOTENCY_KEY_REUSED`, … |
| **423** | The resource is frozen or approved, even with a correct `If-Match` | `RESOURCE_LOCKED`, `WORKFLOW_FROZEN`, `SOW_LOCKED` |
| 428 | `If-Match` missing on a write | `PRECONDITION_REQUIRED` |
| 429 | Rate limited | `RATE_LIMITED` |
| 503 | A dependency is down (Temporal, Keycloak, ClamAV, …); nothing was changed | `ORCHESTRATOR_UNAVAILABLE` (Temporal), … |

### 404, never 403, across tenants

Cross-tenant access, and access to a project you aren't assigned to, return exactly the same 404 as a
resource that doesn't exist. Only `correlation_id` differs (`not_found()`). A 403 would confirm the
resource exists.

## Concurrency: ETag and If-Match

- Every mutable resource returns **`ETag: "<row_version>"`**.
- `PUT` / `PATCH` / `DELETE` on mutable resources require **`If-Match`** with that value
  (`check_if_match`). The order of checks:
  1. frozen or approved → **423**, even if the `If-Match` is correct
  2. missing → **428** `PRECONDITION_REQUIRED`
  3. stale → **412** `PRECONDITION_FAILED`, and the resource stays unchanged
- Exception: SOW saves send `base_version`; a stale base returns **409** with a diff (TD §8.7).

## Idempotency-Key

- Required on commands that start work: **Generate, Retry, Export**.
- The format is a UUID, or 8–64 characters of `A–Z a–z 0–9 _ -` (`validate_idempotency_key`).
- Keys are scoped to **tenant + user + endpoint** and kept for **24 h**.
- Same key + same body → replay: return the **same `202` run** (even while it's still running) and
  don't start new work.
- Same key + different body → **422** `IDEMPOTENCY_KEY_REUSED`. It's never replayed.

## Pagination, filtering, sorting

- `?cursor=&limit=`: `limit` is an integer from **1 to 100** (default **25**). `0`, `101`, negatives or
  non-integers give **400** with `errors[0].path = "limit"`.
- Responses use `Page[T] = {items, next_cursor}`. `next_cursor` is `null` on the last page.
- **Cursors are opaque and signed** (HMAC-SHA256 bound to the tenant). An edited, forged or
  cross-tenant cursor gives **400** `INVALID_CURSOR` and returns no data.
- Filter with `?status=…`. Sort with `?sort=field` or `?sort=-field` (descending). Only fields in each
  resource's allowlist are accepted (`parse_sort`).

## Long-running work

API handlers never parse, call an LLM, generate code or call the sandbox inline. They validate, start a
Temporal workflow and return **202** with a `run` object. Clients poll `GET …/runs/{run_id}`.
