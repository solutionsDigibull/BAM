# API endpoint inventory (generated from the running app)

Source: `discover_routers()` on `origin/dev` @ `d8b0dce`, policy read from each route's dependency chain, 79 routes under `/api/v1` (the two `/health/*` probes are unauthenticated by design and not listed).
Startup already refuses any route without a policy (`main.unguarded_routes`); this table confirms none is missing: 42 `fde`, 25 `admin`, 6 `authenticated`, 4 `super_admin`, 2 self-authenticating (`internal`).

**Review depth:** `live` = exercised against the running API (rounds 2 and 3); `read` = code read, not run; `listed` = inventoried only, **not reviewed in depth**. live: 38, read: 41, listed: 0 (every route now has at least a code read).

**2026-10-29 scope check:** read all 41 `read`-depth routes against the documented product direction
(masters admin, knowledge, SOW, ISATVON, pipeline, temporal). None are dead code, unused, or speculative surface;
all are load-bearing for the pipeline this product describes. The remaining work is depth (promote `read` to
`live`), not scope-trimming. See `07-production-readiness.md` §6.

**Write-control column:** `no If-Match` on a write is not automatically a bug (creates and commands use `Idempotency-Key` or are not versioned resources); it is flagged so the owner can confirm against `docs/api-conventions.md`.

| Module | Method | Path | Policy | Write controls | Depth | Audit notes |
|---|---|---|---|---|---|---|
| builds | GET | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/code/attempts | fde + project_access | - | live | Returns 200 [] for any sub-skill id (own, foreign, random). |
| builds | GET | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/code/attempts/{n}/diff/{m} | fde + project_access | - | live |  |
| builds | GET | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/code/attempts/{n}/files | fde + project_access | - | live |  |
| builds | POST | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/retry | fde + project_access | no If-Match, Idempotency-Key | live | No archive check (F-29); reaches Temporal. |
| filetypes | GET | /api/v1/admin/filetypes | authenticated | - | read | F-17: `authenticated` policy, any signed-in user. |
| filetypes | POST | /api/v1/admin/filetypes/import | super_admin | no If-Match | read | super-admin; shares N-02/N-03 with masters import. |
| identity | GET | /api/v1/admin/audit | admin | - | live |  |
| identity | GET | /api/v1/admin/tenants | super_admin | - | live | Unpaginated (F-18). |
| identity | POST | /api/v1/admin/tenants | super_admin | no If-Match | live | Needs Keycloak admin creds; 503 otherwise. Compensating delete on failed insert. |
| identity | PATCH | /api/v1/admin/tenants/{tenant_id} | super_admin | If-Match | live |  |
| identity | GET | /api/v1/admin/users | admin | - | live |  |
| identity | POST | /api/v1/admin/users/invite | admin | no If-Match | live | F-05: 409 USER_EXISTS checks all tenants (email oracle). |
| identity | PATCH | /api/v1/admin/users/{user_id} | admin | If-Match | live | F-04, F-13: Keycloak calls inside DB tx holding the tenant lock. |
| identity | GET | /api/v1/me | authenticated | - | live | Runs the user-mirror refresh; N-01 race on first calls after login. |
| ingest | POST | /api/v1/internal/tusd/hooks | internal | no If-Match | live | Shared-secret hook; F-14 key reuse with upload-token signer. Live-tested with a real tusd. |
| ingest | GET | /api/v1/projects/{project_id}/documents | fde + project_access | - | live |  |
| ingest | DELETE | /api/v1/projects/{project_id}/documents/{document_id} | fde + project_access | no If-Match | read |  |
| ingest | POST | /api/v1/projects/{project_id}/documents/{document_id}/restore | fde + project_access | no If-Match | read |  |
| ingest | POST | /api/v1/projects/{project_id}/documents/{document_id}/retry | fde + project_access | no If-Match, Idempotency-Key | read |  |
| ingest | POST | /api/v1/projects/{project_id}/uploads | fde + project_access | no If-Match | live | N-05: one audit row per rejected file; F-15 limit race. |
| isatvon | GET | /api/v1/projects/{project_id}/workflows/{workflow_id}/isatvon/export.xlsx | fde + project_access | - | live | Formula guard held; other tenant 404. |
| isatvon | POST | /api/v1/projects/{project_id}/workflows/{workflow_id}/isatvon/validate | fde + project_access | no If-Match | read |  |
| isatvon | PATCH | /api/v1/projects/{project_id}/workflows/{workflow_id}/macro-skills/{macro_id} | fde + project_access | If-Match | live | F-29 no archive check; F-32. |
| isatvon | POST | /api/v1/projects/{project_id}/workflows/{workflow_id}/macro-skills/{macro_id}/regenerate | fde + project_access | no If-Match, Idempotency-Key | live | F-29 no archive check; LLM cost (F-08). |
| isatvon | DELETE | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id} | fde + project_access | If-Match | live | F-29 no archive check; F-32 deadlock -> 500; If-Match optional (documented). |
| isatvon | PATCH | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id} | fde + project_access | If-Match | live | F-29 no archive check; F-32 reorder deadlock -> 500. Mass assignment and dotted paths held. |
| isatvon | GET | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/edits | fde + project_access | - | live |  |
| isatvon | POST | /api/v1/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/regenerate | fde + project_access | no If-Match, Idempotency-Key | live | F-31 idempotency never replays; F-29 reaches Temporal on an archived project. |
| isatvon | GET | /api/v1/schemas/isatvon/{version} | authenticated | - | read |  |
| knowledge | GET | /api/v1/projects/{project_id}/knowledge | fde + project_access | - | read |  |
| knowledge | POST | /api/v1/projects/{project_id}/knowledge/rebuild | fde + project_access | no If-Match, Idempotency-Key | read |  |
| knowledge | GET | /api/v1/projects/{project_id}/knowledge/{kv_id}/graph | fde + project_access | - | read |  |
| knowledge | GET | /api/v1/projects/{project_id}/knowledge/{kv_id}/nodes/{node_id} | fde + project_access | - | read |  |
| knowledge | GET | /api/v1/projects/{project_id}/knowledge/{kv_id}/okf-bundle | fde + project_access | - | read |  |
| masters | GET | /api/v1/admin/master-documents | admin | - | read |  |
| masters | POST | /api/v1/admin/master-documents | admin | no If-Match | read |  |
| masters | GET | /api/v1/admin/master-documents/{document_id} | admin | - | read |  |
| masters | PATCH | /api/v1/admin/master-documents/{document_id} | admin | If-Match | read |  |
| masters | GET | /api/v1/admin/master-types | admin | - | read |  |
| masters | GET | /api/v1/admin/masters/export | admin | - | read |  |
| masters | POST | /api/v1/admin/masters/import | admin | no If-Match | live | N-02 (blocking clamd scan), N-03 (all rows materialised). |
| masters | GET | /api/v1/admin/masters/snapshots/{snapshot_id} | admin | - | read |  |
| masters | GET | /api/v1/admin/masters/template | admin | - | read |  |
| masters | GET | /api/v1/admin/masters/{master_type} | admin | - | read |  |
| masters | POST | /api/v1/admin/masters/{master_type} | admin | no If-Match | read |  |
| masters | GET | /api/v1/admin/masters/{master_type}/{entry_id} | admin | - | read |  |
| masters | POST | /api/v1/admin/masters/{master_type}/{entry_id}/versions | admin | If-Match | read |  |
| notifications | GET | /api/v1/notifications | authenticated | - | live |  |
| notifications | POST | /api/v1/notifications/read-all | authenticated | no If-Match | live |  |
| notifications | POST | /api/v1/notifications/{notification_id}/read | authenticated | no If-Match | live |  |
| pipeline | GET | /api/v1/projects/{project_id}/pipeline | fde + project_access | - | read |  |
| pipeline | POST | /api/v1/projects/{project_id}/pipeline/start | fde + project_access | no If-Match, Idempotency-Key | read | LLM cost: idempotency only, no rate limit (F-08). |
| projects | GET | /api/v1/admin/projects | admin | - | live | Unpaginated (F-18). |
| projects | POST | /api/v1/admin/projects | admin | no If-Match | live |  |
| projects | GET | /api/v1/admin/projects/{project_id} | admin | - | live |  |
| projects | PATCH | /api/v1/admin/projects/{project_id} | admin | If-Match | live |  |
| projects | POST | /api/v1/admin/projects/{project_id}/archive | admin | no If-Match | live |  |
| projects | GET | /api/v1/admin/projects/{project_id}/members | admin | - | live |  |
| projects | PUT | /api/v1/admin/projects/{project_id}/members | admin | no If-Match | live |  |
| projects | GET | /api/v1/projects | fde | - | live | Unpaginated (F-18). |
| registry | GET | /api/v1/admin/registry | admin | - | read |  |
| registry | GET | /api/v1/registry | fde | - | read |  |
| sow | GET | /api/v1/projects/{project_id}/sow | fde + project_access | - | read |  |
| sow | POST | /api/v1/projects/{project_id}/sow/new-version | fde + project_access | no If-Match | read |  |
| sow | POST | /api/v1/projects/{project_id}/sow/regenerate | fde + project_access | no If-Match, Idempotency-Key | read | LLM cost: idempotency only, no rate limit (F-08). |
| sow | GET | /api/v1/projects/{project_id}/sow/versions | fde + project_access | - | read |  |
| sow | POST | /api/v1/projects/{project_id}/sow/versions | fde + project_access | If-Match | read |  |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version} | fde + project_access | - | read |  |
| sow | POST | /api/v1/projects/{project_id}/sow/versions/{version}/approve | fde + project_access | no If-Match | read | F-03/F-33: signal before commit; row lock held across Temporal call. |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version}/citations/{okf_ref_id} | fde + project_access | - | read |  |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version}/citations/{okf_ref_id}/source | fde + project_access | - | read |  |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version}/diff/{other} | fde + project_access | - | read |  |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version}/export | fde + project_access | - | live |  |
| sow | GET | /api/v1/projects/{project_id}/sow/versions/{version}/impact | fde + project_access | - | read |  |
| storage | GET | /api/v1/files/{key:path} | internal | - | live | Signed URL is the credential (<=900 s); no user binding (F-20). |
| temporal | POST | /api/v1/projects/{project_id}/runs/ping | fde + project_access | no If-Match | read | Disabled unless ENABLE_PING; F-19 compose default is true. |
| temporal | GET | /api/v1/projects/{project_id}/runs/{run_id} | fde + project_access | - | read |  |
| workflows | GET | /api/v1/projects/{project_id}/workflows | fde + project_access | - | live |  |
| workflows | PATCH | /api/v1/projects/{project_id}/workflows/{workflow_id} | fde + project_access | If-Match | live | F-29 no archive check; accepts HTML and U+202E in the name. |
