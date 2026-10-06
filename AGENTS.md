<!--
Maintainer notes (HTML comments are stripped before agents see this file).
- Shared rules for every coding agent (Claude Code, Codex, Cursor, Copilot). CLAUDE.md imports this file.
- Keep it under 200 lines. Procedures go in .claude/skills/, path-specific rules in .claude/rules/,
  and anything that MUST happen goes in a hook (.claude/hooks/).
- Add a line when an agent makes the same mistake twice or a reviewer catches something it should
  have known. Delete lines that stop being true.
-->

# AGENTS.md — SBAM (solutionsDigibull/BAM)

Three developers and their agent sessions work on this repo **at the same time**. The plan is split
into phases → 128 GitHub story issues → a dependency graph. Every session does one thing: take one
*unblocked* issue, ship it as one small PR, and never collide with anyone else's work.

## Project

- **SBAM** (Skills Build Acceleration Manager), the build-time layer of BuLLM. Admin creates the tenant
  project and masters. The FDE uploads documents and drives the pipeline: knowledge → SOW → approval.
  **SBAM generates the Workflow from the approved SOW. The FDE never creates a Workflow.** SBAM then
  breaks it into Macro Skills / Sub-Skills (ISATVON 1.4), generates code, tests it in CubeSandbox, and
  certifies and freezes it for SRIM.
- Upstream repo `solutionsDigibull/BAM`, default branch `main`. Developers work from **forks**:
  `upstream` = solutionsDigibull/BAM, `origin` = your fork. PRs always target `upstream/main`.
- **Stack:**
  - `client/`: Next.js App Router + Refine, TypeScript strict, pnpm, Node 22.
  - `server/`: Python 3.12 + uv. FastAPI in `server/api`, Temporal workers in `server/workers`, Pydantic
    contracts in `server/contracts`, Jinja2 templates in `server/templates`.
  - Services: PostgreSQL, Keycloak, Bifrost → OpenRouter, Cognee, ClamAV, tusd, CubeSandbox.
  - Everything runs in Docker Compose (`compose/`, TD §20). `make up` brings up the whole `core` profile. Services
    not built yet run as stubs with their final name, port and healthcheck (TD §20.3.6). Redis is not an SBAM
    dependency (Langfuse-only).
- Read before coding:
  - `docs/ARCHITECTURE_OVERVIEW.md` (golden rules, glossary) and `docs/DEVELOPER_GUIDE.md`.
  - `docs/TEAM_PLAN.md` (who owns what, waves, hand-offs).
  - Your issue's phase page in `docs/phases/` and the `TD §n` pages in `docs/design/` it cites.

## Commands

These targets are **created by the M0 issues with exactly these names**: #32 E1.1 scaffold (done),
#38 E1.9 + #39 E1.10 Docker stack, #33 E1.2 contracts, #43 E1.12 CI. Until they merge, follow the issue text.

- Stack: `make up` · `make ps` · `make down` · `make logs s=<service>` · `make seed` · `make reset CONFIRM=yes`
- One backend test: `cd server && uv run pytest tests/unit/test_x.py -k name`. Prefer this while iterating.
- Integration tests (need `make up`): `make test-int`, or one test with
  `cd server && uv run pytest -m integration tests/integration/test_x.py`. See TD §19.3.
- One frontend test: `pnpm --dir client test src/path/x.test.tsx`
- Regenerate contracts (JSON Schema + TS types): `make contracts`
- **Full checks before pushing:** `make check`. It runs ruff, black --check, mypy --strict, pytest
  (unit/api/contract/temporal), and in the client lint, typecheck, test and build. It also runs the
  contract drift check and the Alembic single-head check.
- Find workable issues: `uv run scripts/unblocked_issues.py` (`--lane D1|D2|D3`, `--all`, `--check N`)

## How work is tracked

- **Milestone** = delivery checkpoint `M0 Foundation` → `M1 Admin + Ingestion` → `M2 Knowledge + SOW` →
  `M3 Breakdown + Build` → `M4 Test + Certify` → `M5 Harden + Go-live` (+ `Future`). Work milestone by milestone.
- **Labels:** `phase:NN` (plan phase 00–16), `area:<module>`, `stream:*`, `priority:P0|P1|P2`,
  `size:S|M|L`, `type:story|spike|chore|epic`.
- **Status labels:** `status:in-progress`, `status:in-review`. `blocked` = waiting on a dependency or decision.
- **Dependency** = the `Blocked by: #12 (`E1.6`), #15 …` line in the issue body, or GitHub's native
  "Blocked by" relationship. Treat both the same.
- **Epics** (`type:epic`) are never implemented directly. They close when their sub-issues close.
- **Lanes:** each story belongs to one developer lane (D1 platform/identity/admin/client shell,
  D2 compose/ingestion/knowledge/SOW/ISATVON/testgen, D3 contracts/orchestration/build/certify).
  See `docs/TEAM_PLAN.md` and `docs/team-lanes.json`. Take your own lane first. Take another lane's issue
  only when yours has nothing unblocked, and say so on the issue.
- An issue is workable only when it is open, not an epic, unassigned, has no `status:*` label, and
  every issue in its `Blocked by` is closed.

## Session workflow

1. **Pick:** find an unblocked issue (`uv run scripts/unblocked_issues.py --lane Dn`) and claim it:
   assign yourself, add `status:in-progress`, then re-check that you are the only assignee.
   Do not write code until the claim succeeds.
2. **Isolate:** one issue = one branch = one worktree. Branch `<issue>-<short-slug>` from fresh
   `upstream/main`, e.g. `61-tusd-upload-hooks`.
3. **Understand:** read the whole issue: scope, input/output checks, safety checks, edge cases, test
   cases, acceptance criteria. Also read the merged PRs of its blockers so you build on the real contracts.
4. **Plan:** post a 2–5 line plan and the expected file list as an issue comment before editing. If
   the change touches more than one `area:` or any hot file, confirm the plan with the user first.
5. **Build:** implement every acceptance criterion. Implement every listed test case and put its ID in
   the test name, e.g. `test_e4_2_t02_oversize_rejected`. Tick the safety checks as you enforce them.
6. **Ship:** `make check`, rebase on `upstream/main`, then open the PR with `Closes #N`. Move the label
   to `status:in-review`.

## Working in parallel without collisions

- Change only the files the issue needs. No drive-by refactors, renames or reformatting. Open a new
  issue for those instead.
- Before editing a file, check whether an open PR already touches it:
  `gh pr list -R solutionsDigibull/BAM --state open --search "<path>"`. If one does, keep your diff to
  that file minimal and mention the overlap in your PR.
- **Hot files have owners** (docs/TEAM_PLAN.md §G). Changing a hot file you don't own needs that owner's review:

| Hot file / area | Owner |
|---|---|
| `server/contracts/**` (and generated `client/src/contracts/**`) | D3 |
| `server/workers/sbam_workers/pipeline/**`, `server/workers/sbam_workers/bootstrap.py` | D3 |
| state machine tables, `server/api/sbam_api/audit/**` | D3 |
| `server/api/sbam_api/main.py`, `server/api/sbam_api/core/**`, Alembic base / `migrations/env.py` | D1 |
| `client/src/app/layout.tsx`, navigation, `client/src/providers/**` | D1 |
| `.github/**`, `pyproject.toml`, lockfiles | D1 |
| `compose/compose*.yaml`, `compose/.env.example`, `Makefile` | D2 |

- **Contract first:** a change to a shared contract (Pydantic model, API schema, DB table used by
  another module, pipeline activity signature) lands in its own small PR before the code that uses it.
- **Never edit the ProjectPipeline workflow to add your stage.** Implement your activity in your own
  module (`ingest/`, `knowledge/`, `sow/`, `isatvon/`, …). It registers itself, and D3 wires it.
- **Never register routes in `main.py`.** Each API module exposes `router`, and it is auto-discovered.
- **Your service is your block.** If your issue owns a Compose service (TD §20.3.6), replace only that service's
  block in `compose/compose.yaml` and its folder under `compose/`. Keep the name, port and healthcheck, and keep
  `make up` healthy. New env vars go in `compose/.env.example` with a comment and a safe default.
- **Tests that need a running service** are `@pytest.mark.integration` and run against the Compose stack
  (`make test-int`). Unit and API tests use fakes and never need Docker (TD §19.3).
- If you need something another in-progress issue is building, do not build it too. Code against
  the contract or a stub, say so in the PR, and comment on the other issue.

## Golden rules (non-negotiable)

- Every repository query is tenant-scoped through the base repository. Cross-tenant or unassigned
  access returns **404**, never 403.
- `server/contracts` (Pydantic v2) is the single source of truth. Never duplicate a contract model.
  Never hand-edit `client/src/contracts/`; regenerate it.
- Long work (parsing, LLM, codegen, sandbox) runs only in Temporal activities. API handlers never block.
- Temporal workflow code is deterministic: no I/O, no `datetime.now()`, no randomness. Use `workflow.patched`
  for changes, and replay tests must pass.
- LLM calls go only through the Bifrost client with an alias (`sbam-ocr`, `sbam-sow`, `sbam-isatvon`,
  `sbam-codegen`, `sbam-testgen`, `sbam-corrector`). No direct SDK calls. CI uses the mock provider.
- Project files go only through `StorageBackend`. No direct file I/O.
- State changes go through the state-machine service and write the audit log.
- API: base `/api/v1`. Errors are problem+json. Writes use `ETag`/`If-Match`. Long operations return 202
  with a run object. Start commands take an `Idempotency-Key`.
- Certified/frozen versions are immutable (**423**). `local-docker` sandbox runs never certify.
- Secrets come from env/Docker secrets only. Never commit `.env*`.

## Branches, commits, PRs

- Branch `<issue>-<short-slug>` from fresh `upstream/main`. Rebase at least daily (`git fetch upstream
  && git rebase upstream/main`). Never merge `main` into a branch. Aim to merge within 1–3 days.
- Conventional Commits with the area as scope and the issue number:
  `feat(ingestion): add tusd pre-create hook (#61)`.
- One issue → one PR. Aim for ≤ 400 changed lines, excluding generated files and lockfiles. Above ~600, split
  it (contract → backend → UI), using stacked PRs only within your own lane.
- PR title = the issue title (`[E4.2] …`). The body uses `.github/pull_request_template.md` and starts
  with `Closes #N`. Squash merge.
- Never mark a PR ready while CI is red.

## Definition of Done

- Every acceptance criterion is met. Every issue test case is automated and passing (or, for `manual`,
  recorded in the PR). Every safety check is ticked.
- `make check` passes locally and in CI. Rebased on `upstream/main` with no conflicts.
- API changes appear in OpenAPI and the TS types are regenerated. DB changes come with **one** reversible
  Alembic migration.
- Tenant/RBAC/IDOR tests exist for new endpoints. UI has loading, empty, error and forbidden states.
- Docs or an ADR are updated if behaviour, setup or env vars changed. No unrelated changes in the diff.
- Issues this one blocked are checked. Comment on them when they are now unblocked, and remove their
  `blocked` label if all of their blockers are closed.

## When blocked, stuck, or stopping

- Missing dependency discovered: add it to the issue's `Blocked by` line, add `blocked`, comment why,
  unassign yourself, remove `status:in-progress`, and pick another issue.
- Out-of-scope work found: open a new story under the right epic (`[E<epic>.<n>] …` with labels,
  milestone, acceptance criteria and `Blocked by`). Do not grow the current PR.
- Ending before done: push the branch and leave a hand-off comment (done / left / gotchas).
- Spec unclear, or an issue contradicts the docs: comment on the issue or ask the user. Design decisions
  go in an ADR (`docs/adr/`). Don't guess.

## Hard rules

- Never push to `main` (upstream or fork). Never force-push. Use `--force-with-lease` on your own branch only.
- Never work on an issue assigned to someone else, or on one with open blockers.
- Never close issues by hand. Merged PRs close them. Never admin-merge past branch protection.
- Never skip hooks (`--no-verify`). Never commit secrets or `.env*` files.
- Never edit CI, branch protection or release config unless the issue is about exactly that.

## Repo-specific gotchas

- Issue text that says `apps/web`, `services/api`, `services/workers`, `packages/contracts` or `deploy/`
  means `client/`, `server/api`, `server/workers`, `server/contracts` and `compose/` (TD §6).
- Milestone order beats phase number. E10.1 (phase 10) is M1, and E11.1/E12.1 are M2 because others depend on them.
- Two developers adding Alembic migrations in parallel creates two heads. Generate your revision
  right before merge, after rebasing. Never use `alembic merge`.
- Lockfile conflict on rebase: take `upstream/main`'s lockfile and re-run `uv lock` / `pnpm install`.
- CubeSandbox is not available on laptops. Use the `sandbox-local` profile, which is non-certifying.
- Fixtures (ISATVON 1.4 sample MS01.1, masters workbook, EICAR file) live in `server/tests/fixtures`.
- On Windows use Git Bash or WSL2 for `make` and the scripts.
