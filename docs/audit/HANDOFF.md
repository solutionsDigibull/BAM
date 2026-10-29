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
Done 2026-10-29: real Temporal hung-activity test (result in 01-findings.md, round 4), Cognee live results in 01-findings.md,
leftover `zz_audit_real_server_test.py` removed (copy kept in poc/), llm-mock confirmed not running.
Still open:
1. Needs owner approval: comment on #450 and #435 with the Cognee live results (F-27, F-09).
2. Blocked: real-model prompt injection and CubeSandbox (no model path, sandbox not built).
3. Needs owner approval: push the audit and docdrift branches, open the doc-fix issue and PR, revert the old dirty tree.
