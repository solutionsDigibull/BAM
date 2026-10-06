---
name: ship
description: Finish the current SBAM issue - verify the issue's acceptance criteria, test cases and safety checks, run make check, rebase on upstream/main, self-review the diff, push to the fork, open a PR against solutionsDigibull/BAM with "Closes #N", and move the issue to status:in-review. Use when the user says "ship it", "open the PR", "I'm done", or all acceptance criteria are met.
---

# Ship the current issue as one PR

The issue number is the prefix of the current branch (`<N>-<slug>`). If the branch has no number prefix,
ask the user which issue this is before continuing. All `gh` issue and PR commands use `-R solutionsDigibull/BAM`.

## 1. Check the Definition of Done

Run `gh issue view <N> -R solutionsDigibull/BAM` and walk through, against the diff:

- every **Acceptance criterion**;
- every row of the **Test cases** table. Each one needs an automated test whose name or docstring contains
  its ID (`e4_2_t02`). `manual` cases need their measured result for the PR body;
- every **Safety check**. Tick each one on the issue as you confirm it is enforced and tested;
- the Definition of Done in AGENTS.md.

If anything is missing, finish it first or tell the user.

## 2. Rebase on the latest main

```bash
git fetch upstream
git rebase upstream/main
```

Resolve conflicts by keeping both sides' intent. Special cases:

- **Lockfiles:** take upstream's version, then re-run `uv lock` / `pnpm --dir client install`.
- **Alembic:** if another migration merged first, delete yours and regenerate it on top. Keep a single head.
- **Generated contracts:** regenerate with `make contracts`. Never hand-merge them.
- **Code owned by another open PR or another lane's hot file:** stop and ask the user.

## 3. Run the full checks

`make check`. Everything must pass. Never use `--no-verify`. Rerun after any rebase that pulled in changes.

## 4. Self-review the diff

```bash
git diff upstream/main...HEAD --stat
git diff upstream/main...HEAD
```

- Remove anything unrelated to the issue: formatting churn, debug code, unrelated renames.
- If the diff is well over ~400 changed lines (generated files and lockfiles excluded), propose a split to
  the user before opening the PR: contract → backend → UI.
- List the hot files touched (AGENTS.md table and `.claude/rules/shared-files.md`) and their owners.
- Check the golden rules: tenant scoping (404), Bifrost aliases only, no I/O in workflow code, `StorageBackend` only,
  problem+json errors, one reversible migration.

## 5. Push and open the PR

```bash
git push -u origin HEAD          # origin = your fork
gh issue view <N> -R solutionsDigibull/BAM --json title -q .title
gh issue list -R solutionsDigibull/BAM --state open --search "\"#<N>\" in:body" --json number,title \
  --jq '.[] | "#\(.number) \(.title)"'          # issues that list this one under Blocked by
```

Fill `.github/pull_request_template.md` into a temp file, then:

```bash
gh pr create -R solutionsDigibull/BAM --base main --head <your-login>:<branch> \
  --title "<issue title, e.g. [E4.2] tusd service + hooks + upload token + limits>" --body-file <file>
```

The body starts with `Closes #<N>`. Fill in Builds on, Unblocks, How to test, test-case IDs and Hot files touched.
Use `--draft` if anything is knowingly incomplete, and say what in the body.

## 6. Update the issue

```bash
gh issue edit <N> -R solutionsDigibull/BAM --add-label status:in-review --remove-label status:in-progress
```

## 7. Report

Give the user:

- the PR URL;
- the CI status (`gh pr checks -R solutionsDigibull/BAM <pr>`);
- the issues this unblocks on merge, and which lane owns each;
- any follow-up issues you created.

After the merge, the author comments on each unblocked issue and removes its `blocked` label if all of its
blockers are closed.
