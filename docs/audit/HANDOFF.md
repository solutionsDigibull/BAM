# Handoff and decisions (updated 2026-10-29, afternoon)

Rules from the owner: if a task can't finish in about 5 minutes, defer it, write it here, and move on.
**Update this file after every completed step, not at the end.**

## State
- Audit branch `audit/full-security-review` (worktree `/home/Fool/Arch - Work/BAM-audit`): pushed, **PR #466** into `dev`. Docs only.
- Doc-drift fix: branch `fix/db-design-doc-drift-v2` (worktree `/home/Fool/Arch - Work/BAM-docdrift`), issue #464, **PR #465** into `dev`.
- Issues filed from the audit: #427-#461, #464. Comments on #118, #120, #353, #355, #421, #435, #450, #457.
- Old dirty tree `/home/Fool/Arch - Work/BAM` (branch `fix/db-design-doc-drift`): cleaned (patch kept at `/tmp/old-docdrift-dirty-tree.patch`). Untracked: `all-branches/` (see Pending 2), `SESSION_STATE.md`, `.claude/skills/security-audit/` (auto-materialized skill reference, leave).

## Decisions made
- **PRs target `dev`, not `main`.** `AGENTS.md` says `upstream/main`, but `dev` is 462 commits ahead of `main` and every recent PR merges into `dev`. (Worth a docs fix in `AGENTS.md`.)
- This account has WRITE on `solutionsDigibull/BAM`: branches are pushed to `origin` directly (never `main`/`dev`), no fork.
- Audit target is `origin/dev`. Live tests only on local throwaway stacks, never the owner's running stack.
- `is_locked` / `workbook_version` columns dropped; TD §7 corrected instead (PR #465).
- Existing issues get comments, not duplicates. No `priority:P3` label exists: P3 stated in body.
- Owner declined a live read-a-host-file exploit demo; live tests use harmless arguments only. Findings are proven by showing the gap, not by exfiltrating content.
- CI `image-scan` failure on #465/#466 (CRITICAL in a Rust crate bundled in `temporalio`) is pre-existing and unrelated; owner said to ignore it for now.
- CubeSandbox work waits until all other findings are resolved (owner).

## Pending
1. DONE: all 21 drafts are filed (#427-#447), plus A-22..A-35 (#448-#461). Cross-check of every finding ID: all covered; F-16 was traced and refuted (no issue), N-07 is a review note on #353.
2. DONE: `all-branches/` (427 MB, 42 snapshots of old merged branches, Oct 13). 13,594 of 13,602 files are byte-identical to blobs in git. The other 8: 7 early drafts of the #49 identity work (superseded on `dev`; `0003_identity.py` was renumbered) and `dev/compose/.env.local`, a gitignored file with a REAL OpenRouter key and generated passwords, the only copy. Archived (not deleted) to `/home/Fool/Arch - Work/_archive/BAM-all-branches-2026-10-13.tar.zst` (mode 600, 13,602 files verified, env file checksum matched), then the directory was removed. Owner: rotate that OpenRouter key if it is still live, and delete the archive when no longer wanted.
3. DONE: the "25 of 79 inventoried only" line was stale; every route is `read` or `live` (0 `listed`). Reviewed all 41 `read`-depth routes against the product direction: none are weeds, all are load-bearing. Fixed the stale line in README.md and 05's summary.
4. DONE: `07-production-readiness.md` written - sequencing recommendation (not a new finding): fix F-25 (deploy blocker) now; treat F-01/F-02/F-36 as a blocking dependency of real certification, not independent backlog; F-26/F-27 (data lifecycle) before real customer data; F-09/#437/#440/F-35 hygiene; D-18 and F-23 are owner decisions, not bugs.
5. Blocked: real-model prompt injection, CubeSandbox (owner: wait until other findings resolved).
6. Open design questions in `06-flows-and-design-review.md` (e.g. D-18 second approver).
