<!--
CLAUDE.md for Claude Code. The shared rules live in AGENTS.md so that every agent tool reads the same
ones. Keep only Claude-specific mechanics here. Personal preferences go in CLAUDE.local.md (gitignored).
-->

@AGENTS.md

## Claude Code specifics

- **Start every session with `/next-issue`** (or `/next-issue 61` for a named issue, or
  `/next-issue --lane D2`). It syncs, checks the dependency graph, claims the issue, sets up the
  worktree and posts the plan. Do not write code before the claim succeeds.
- **Finish with `/ship`.** It runs `make check`, rebases on `upstream/main`, self-reviews the diff, opens the PR
  against `solutionsDigibull/BAM` and moves the issue to `status:in-review`.
- **One worktree per issue.** If this session is in the main checkout, use the EnterWorktree tool
  named `<issue>-<slug>`, or ask the user to restart with `claude --worktree <issue>-<slug>`.
  Worktrees live in `.claude/worktrees/`. `.worktreeinclude` copies the local `.env` files into them.
- Use plan mode for any issue that touches more than one `area:` or a hot file.
- Path-scoped rules load automatically when you open matching files:
  - `.claude/rules/shared-files.md`: hot files, lockfiles, migrations, CI.
  - `.claude/rules/backend.md`: everything under `server/`.
  - `.claude/rules/temporal.md`: workflows and activities.
  - `.claude/rules/frontend.md`: everything under `client/`.
- Hooks in `.claude/settings.json` enforce the hard rules:
  - `guard-git.mjs` blocks pushes to `main`, force pushes, `--no-verify`, `gh issue close` and admin merges.
  - `guard-files.mjs` blocks edits to generated contracts and `.env` files.
  - A blocked command means change approach, not look for a workaround.
- Use `gh` with `-R solutionsDigibull/BAM` for issues and PRs, because inside a fork `gh` defaults to the fork.
- Run single test files while iterating. Run `make check` only before shipping.
- After editing AGENTS.md, CLAUDE.md or the rules, run `/context` to confirm what loaded.
