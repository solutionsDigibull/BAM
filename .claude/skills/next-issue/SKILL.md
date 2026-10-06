---
name: next-issue
description: Find the best unblocked SBAM GitHub issue in the dependency graph (preferring the developer's own lane), claim it so parallel sessions don't collide, set up an isolated worktree branch and post the plan. Use at the start of a work session, when the user says "pick an issue", "what should I work on", "take issue #N", or after finishing a PR.
---

# Pick and claim the next issue

Arguments: `$ARGUMENTS`, which may be an issue number (`61`) and/or a lane (`--lane D2`). Follow the steps in
order. Stop and report to the user at any step that fails. Never skip the claim.

All `gh` issue and PR commands use `-R solutionsDigibull/BAM`, because inside a fork `gh` defaults to the fork.

## 1. Sync

```bash
git remote get-url upstream || git remote add upstream https://github.com/solutionsDigibull/BAM.git
git fetch upstream --prune
```

## 2. Choose

- **Lane:** use `--lane` if given. Otherwise ask the user once which lane they are (D1, D2 or D3, see
  `docs/TEAM_PLAN.md`) and suggest they save it in `CLAUDE.local.md` as `My lane: Dn`.
- **Issue number given:** run `uv run scripts/unblocked_issues.py --check <N>`. If it exits non-zero,
  show the reasons, then show the top 3 rows of `uv run scripts/unblocked_issues.py --lane <lane>` as
  alternatives and stop.
- **No number given:** run `uv run scripts/unblocked_issues.py --lane <lane>`. It is already sorted best-first:
  milestone ↑, priority ↑, number of issues it unblocks ↓, then the wave from TEAM_PLAN. If the lane has nothing,
  run it without `--lane` and pick from another lane. Say so on the issue when you claim it.
- Check which areas are busy and prefer an issue whose `area:` has no in-progress work:

  ```bash
  gh issue list -R solutionsDigibull/BAM --state open --label status:in-progress --json number,labels \
    --jq '[.[].labels[].name | select(startswith("area:"))] | group_by(.) | map({(.[0]): length}) | add'
  ```

- Show the user the top 3, each with a one-line reason, and proceed with the first unless they choose another.

## 3. Claim (race-safe)

```bash
gh issue edit <N> -R solutionsDigibull/BAM --add-assignee @me --add-label status:in-progress
gh issue view <N> -R solutionsDigibull/BAM --json assignees --jq '[.assignees[].login]'
```

If anyone other than you is now assigned, another session claimed the issue at the same moment. Undo yours
(`--remove-assignee @me --remove-label status:in-progress`) and go back to step 2 with the next candidate.

## 4. Understand before coding

```bash
gh issue view <N> -R solutionsDigibull/BAM --comments
```

- Read the whole body: Scope, Inputs and input validation, Outputs and output checks, Safety checks,
  Edge cases, Test cases, Acceptance criteria and References.
- Open the phase page (`docs/phases/…`) and every `TD §n` page it cites (`docs/design/`).
- For each issue in `Blocked by`, read its merged PR so you build on the real contracts:
  `gh pr list -R solutionsDigibull/BAM --state merged --search "<blocker-number>"`.
- If the acceptance criteria are missing or contradict the docs, ask the user before continuing.

## 5. Isolate

The session must work in its own worktree, on branch `<N>-<short-slug>` based on a fresh `upstream/main`.

- If the session is in the main checkout, enter a worktree named `<N>-<short-slug>` with the EnterWorktree
  tool (or the user restarts with `claude --worktree <N>-<short-slug>`). Then run
  `git switch -C <N>-<short-slug> upstream/main`.
- If the session is already in a worktree on another issue's branch, stop and tell the user: one issue per worktree.
- Install dependencies in the new worktree once they exist: `cd server && uv sync` and `pnpm --dir client install`.

## 6. Announce the plan

Check for open PRs touching the files you expect to change (`gh pr list -R solutionsDigibull/BAM --state open
--search "<path>"`) and whether any of them are hot files you don't own (AGENTS.md table). Then post:

```bash
gh issue comment <N> -R solutionsDigibull/BAM --body "Claimed by @$(gh api user -q .login) via Claude Code.
Plan: <2–5 lines>
Expected files: <paths>
Hot files touched: <none | path (owner Dn)>
Branch: <N>-<short-slug>"
```

Tell the user the issue, the plan and any risks: hot files, contract changes, overlapping PRs, or a hand-off
from another lane that isn't merged yet.
