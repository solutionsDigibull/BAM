# Handoff and decisions (updated 2026-10-29)

Rule from the owner: if a task can't finish in about 5 minutes, defer it, write it here, and move on.

## State
- Audit branch `audit/full-security-review` (worktree `/home/Fool/Arch - Work/BAM-audit`): docs only, committed locally, **not pushed**.
- Issues filed on GitHub: #427-#461 (35 new), comments on #118, #120, #353, #355, #421, #450.
- Doc-drift branch `fix/db-design-doc-drift-v2` (worktree `/home/Fool/Arch - Work/BAM-docdrift`), commit `da40568` on `origin/dev`: TD §7 aligned with the real schema. **Not pushed, no PR, no issue yet.**
- Original dirty tree `/home/Fool/Arch - Work/BAM` (branch `fix/db-design-doc-drift`): untouched backup. Its `is_locked`/`workbook_version` migration (0024) collides with dev's `0024_document_parse` and is not needed.

## Decisions made
- Audit target is `origin/dev`, not `main`. Local-only throwaway stacks for all live tests; never the owner's running stack.
- `is_locked` / `workbook_version` columns dropped; doc corrected instead.
- Existing issues get comments (#355, #421, #353, #118, #120, #450), not duplicates. No `priority:P3` label exists: P3 stated in body.
- Pushing, PRs, and reverting the dirty tree need explicit owner approval.

## Pending (deferred)
1. **Real Temporal hung-activity test** (`docs/audit/poc/temporal/audit_real_server_test.py`): was running ~20 min in the background (log `/tmp/audit-out/real-temporal.log`, look for `RESULT`). Kill leftover `pytest ... zz_audit_real_server_test.py` and the local dev server on :57234 if still running; delete `server/tests/temporal/zz_audit_real_server_test.py` from the audit worktree (it is copied into poc/).
2. **Cognee live (done, needs doc entry):** with real Cognee 1.6.2 + the repo's LLM mock: F-27 confirmed live (removed doc A still returned by search and still in graph after rebuild); project isolation held; default `COGNEE_USER_SECRET` derives the same project password as a deployed default (F-09 confirmed); `delete_dataset` works. Script: `poc/temporal/cognee_live.py`. Add to `01-findings.md` and comment on #450 and #435.
3. Real-model prompt injection and CubeSandbox: blocked (no model path, sandbox not built).
4. Decide: push audit branch + docdrift branch? Open an issue and PR for the doc fix? Revert the old dirty tree?
5. Cleanup: stop llm-mock (pid in /tmp/audit-cognee/mock.pid) if running.
