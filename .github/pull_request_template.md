Closes #<!-- issue number -->

## What and why
<!-- 1–3 sentences. Link the plan comment on the issue if useful. Mention decision IDs (C-xx, O-xx) implemented. -->

## Dependencies
- Builds on: <!-- #N, #M: merged blockers whose contracts this uses -->
- Unblocks: <!-- #N (lane Dx), #M (lane Dy): issues that can start once this merges -->

## How to test
<!-- Commands a reviewer can run, e.g. `make up && cd server && uv run pytest tests/api/test_documents.py` -->

## Test cases from the issue
<!-- List the issue's test-case IDs and where each is implemented; paste measured results for `manual` cases. -->
- `E?.?-T01` → `server/tests/...::test_e?_?_t01_...`

## Hot files touched
<!-- none | server/contracts/... (owner D3), migrations/versions/xxxx (one revision), compose/compose.yaml (owner D2), ... -->

## Checklist
- [ ] Every acceptance criterion is met; every issue test case is automated (or recorded, for `manual`)
- [ ] Every safety check in the issue is enforced and ticked
- [ ] `make check` passes; rebased on latest `upstream/main`; no unrelated changes
- [ ] API change → OpenAPI updated and `make contracts` re-run; DB change → one reversible migration
- [ ] Tenant/RBAC/IDOR tests for new endpoints; UI has loading/empty/error/forbidden states
- [ ] Docs / ADR / `.env.example` updated if behaviour or setup changed
