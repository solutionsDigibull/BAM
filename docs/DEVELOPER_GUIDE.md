# Developer Guide — working on SBAM issues

This guide covers how to pick an issue, implement it in your fork and get it merged into `solutionsDigibull/BAM`.

---

## 1. How the work is organised

| Concept | What it means | Where to see it |
|---|---|---|
| **Epic** (`type:epic`) | One per plan phase (Phase 0–16). Never implemented directly; it closes when all its sub-issues close. | Issues filtered by `label:type:epic`; each epic lists its sub-issues |
| **Story** (`type:story`, `type:spike`, `type:chore`) | One unit of work of 1–5 days, with acceptance criteria. **This is what you pick up.** | Sub-issues of an epic |
| **Milestone** | Delivery checkpoint ending in a demo: M0 Foundation → M1 Admin + Ingestion → M2 Knowledge + SOW → M3 Breakdown + Build → M4 Test + Certify → M5 Harden + Go-live (+ Future) | Milestones tab |
| **Stream** (`stream:*`) | Who usually owns it: **A** platform/DevOps/security, **B** knowledge/LLM, **C** build pipeline, **D** frontend | Labels |
| **Priority** | `priority:P0` (MVP critical path), `P1` (MVP, not on the critical path), `P2` (future) | Labels |
| **Size** | `size:S` ≤1 day, `size:M` 2–3 days, `size:L` 4–5 days | Labels |
| **Phase** | `phase:00` … `phase:16` (matches the plan phase) | Labels |
| **Area** | `area:ingestion`, `area:codegen`, … (functional area) | Labels |
| **Blocked** | `blocked` = has unmerged dependencies listed in the issue body under **Blocked by** | Labels + issue body |

Useful issue searches (paste into the Issues search box):

```text
is:open label:priority:P0 -label:blocked no:assignee milestone:"M0 Foundation"     ← ready to pick, current milestone
is:open label:stream:B-knowledge -label:blocked no:assignee                         ← ready to pick in your stream
is:open assignee:@me                                                                ← your work
is:open label:blocked                                                               ← waiting on dependencies
```

---

## 2. Picking an issue

1. **Work in milestone order.** Take issues from the current milestone first (see [ISSUE_MAP.md](ISSUE_MAP.md)).
2. **Check that it's unblocked.** Every issue under **Blocked by** must be closed. If they're all closed but the `blocked` label is still there, remove the label.
3. **Prefer your stream, but help where the critical path needs it.** Stream A carries a lot of M0 work. B and D developers should pick up M0 platform stories (Compose profiles, CI, fixtures) when their own stream is blocked.
4. **Assign yourself** before starting. If you can't self-assign, comment `I'm taking this`.
5. **One P0 story at a time per developer.** Finish or hand back before taking another P0.
6. **Read before coding.** Read the sections listed under **Docs** in the issue (`TD §…`, `PLAN …`), the parent epic's goal and exit criteria, and the rules in [ARCHITECTURE_OVERVIEW.md §7](ARCHITECTURE_OVERVIEW.md#7-golden-rules-non-negotiable).
7. **If the story is bigger than its size label**, split it: create sub-issues under the same epic, using the same title format, and link them in a comment.

---

## 3. Fork → branch → PR workflow

```text
solutionsDigibull/BAM (upstream, main)  ◄──── Pull Request ────  <you>/BAM (fork)  ◄── push ── local branch
```

### One-time setup

```bash
gh repo fork solutionsDigibull/BAM --clone          # creates <you>/BAM and clones it
cd BAM
git remote -v                                       # origin = your fork, upstream = solutionsDigibull/BAM
```

### For every issue

```bash
git fetch upstream
git switch -c 42-tusd-upload-hooks upstream/main    # branch: <issue-number>-<short-slug>
# … implement, commit …
git push -u origin 42-tusd-upload-hooks
gh pr create --repo solutionsDigibull/BAM --base main \
  --title "[E4.2] tusd service + hooks + upload token + limits" \
  --body "Closes #42"
```

Keep your branch current with `git fetch upstream && git rebase upstream/main`. Never merge `main` into feature branches.

### Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) with the area as scope and the issue number:

```text
feat(ingestion): add tusd pre-create hook with size and count limits (#42)
fix(codegen): keep protected skeleton regions intact during assembly (#108)
test(identity): add cross-tenant IDOR cases for projects API (#51)
docs(adr): record Keycloak organizations tenancy decision (#14)
```

Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `ci`, `build`, `perf`.

### Pull request rules

- **Title:** the issue title (`[E4.2] …`).
- **Body:** `Closes #<issue>`. Then a short *what/why*, *how to test* and screenshots for UI.
- **Size:** under ~600 changed lines (excluding generated files). Split bigger work into stacked PRs.
- **Review:** at least **1 approving review**. Changes to `server/contracts/` (shared contracts) need a reviewer from stream C plus one from the consuming stream.
- **Merge:** squash merge; delete the branch.
- **After merging,** look for issues your PR unblocks (search `is:open label:blocked "#42"`). Remove `blocked` from any whose blockers are now all closed.

---

## 4. Definition of Done (every story)

A PR is mergeable only when all of the following hold (from PLAN §26.4):

- [ ] Acceptance criteria in the issue are all ticked.
- [ ] Linked to the issue (`Closes #n`); the PR description references any decision IDs (`C-xx`, `O-xx`) it implements.
- [ ] API changes appear in OpenAPI, and TypeScript types are regenerated (no hand-edited generated files).
- [ ] Tenant/project authorization is implemented and covered by RBAC/IDOR tests.
- [ ] Server-side Pydantic validation is in place for all inputs.
- [ ] DB changes come with a reversible Alembic migration.
- [ ] State changes go through the state-machine service and write the audit log.
- [ ] Errors use problem+json and map to the failure matrix (TD §18) with user-readable messages.
- [ ] Logs/traces carry tenant, project, workflow and correlation IDs, and LLM calls are traced (Langfuse).
- [ ] Tests: unit + integration; Temporal workflow tests for workflow changes; E2E for user journeys.
- [ ] UI work has loading, empty, error and forbidden states.
- [ ] Docker image builds and passes the Trivy scan; `make up` still starts the stack.
- [ ] Docs/ADRs updated where behaviour or decisions changed.
- [ ] CI is green.

---

## 5. Coding conventions

### Python (`server/`)

- Python 3.12, **uv** for dependencies, **Ruff** + **Black** + **MyPy `--strict`**, **pytest**.
- FastAPI modules follow `router → service → repository`. Routers contain no business logic.
- **Every** repository query is tenant-scoped through the base repository. Cross-tenant or unassigned access returns **404**.
- Pydantic v2 models in `server/contracts` are the single source of truth. Never duplicate a contract model elsewhere.
- No direct file I/O for project data. Use `StorageBackend`.
- No direct LLM SDK calls. Use the Bifrost client with an **alias** (`sbam-ocr`, `sbam-sow`, `sbam-isatvon`, `sbam-codegen`, `sbam-testgen`, `sbam-corrector`).
- Long-running work runs in Temporal activities only. API handlers never block on parsing, LLM, codegen or sandbox calls.
- Temporal workflow code must be deterministic: no I/O, no `datetime.now()`, no randomness. Use `workflow.patched` for changes, and replay tests must pass.
- Secrets come from environment/Docker secrets only. Never commit `.env`.

### TypeScript (`client/`)

- Next.js App Router, TypeScript strict, ESLint + Prettier, Vitest + Testing Library, Playwright for E2E.
- Data access goes through the Refine data provider and generated types in `client/src/contracts/`, which must never be edited by hand.
- AG Grid, TipTap, Monaco, Uppy and Xyflow are client components (`"use client"`, dynamic import with `ssr: false`).
- Tokens stay server-side (Auth.js session plus the `/api/proxy` route). Never store tokens in browser storage.
- The UI hides actions the user can't perform, but the backend is always authoritative.

### API

- Base `/api/v1`. **P** = `/projects/{project_id}` (documents, knowledge, SOW, pipeline). **W** = `/projects/{project_id}/workflows/{workflow_id}` (Workflow-level resources).
- Errors use problem+json; mutable resources use ETag/If-Match; long operations return 202 with a run object; commands that start work take an `Idempotency-Key` (TD §8.1).

### Tests

| Kind | Where | Runs |
|---|---|---|
| Unit / API / contract | `server/tests/{unit,api,contract}` | every PR |
| Temporal (incl. replay) | `server/tests/temporal` | every PR |
| Frontend components | `client/src/**/*.test.tsx` | every PR |
| E2E (E01–E20) | `client/e2e` | nightly + release |
| LLM golden sets | `server/tests/llm-golden` | on prompt/model change |
| Security / performance | `server/tests/{security,perf}` | nightly / release |

LLM-dependent tests run against the **mock LLM provider** in CI (issue E1.15). Fixtures (ISATVON 1.4 sample MS01.1, masters workbook, sample client documents, EICAR test file) live in `server/tests/fixtures` (issue E0.8).

---

## 6. Local development

> Available once **E1.10** (Compose core profile) is merged. Until then, follow the instructions in the M0 issues.

Prerequisites:
- Docker Desktop (WSL2 on Windows) with at least 32 GB RAM on the machine.
- Python 3.12 + uv.
- Node 22 + pnpm.
- make.
- Netbird client, for access to the dev VM and the CubeSandbox host.

```bash
cp compose/.env.example compose/.env      # fill the OpenRouter key (or leave the mock provider on)
make up                                   # core profile: postgres, keycloak, temporal, bifrost, clamav, tusd, api, workers, web
make seed                                 # imports the consolidated masters workbook + demo tenant/users
make test                                 # unit + API + contract + temporal tests
make logs                                 # follow logs
make down                                 # stop (data kept);  make reset = destroy volumes (asks first)
```

| URL (local) | What |
|---|---|
| http://localhost:3000 | SBAM web (Admin login and FDE login) |
| http://localhost:8000/docs | API (OpenAPI) |
| http://localhost:8080 | Keycloak admin |
| http://localhost:8233 | Temporal UI |
| http://localhost:3001 / :3002 | Langfuse / Grafana (profile `obs`) |

The CubeSandbox host isn't available on laptops. Use the `sandbox-local` profile (non-certifying local-docker runner). Certification only happens on the real CubeSandbox host.

---

## 7. Getting help and changing the plan

- **Questions about an issue:** comment on the issue and mention the epic owner or stream lead.
- **The spec seems wrong or something is missing:** comment on the issue. If it's a design decision, open an ADR PR (`docs/adr/`, see [adr/README.md](adr/README.md)) and link it.
- **New work discovered:** open a new story under the right epic. Use the title format `[E<epic>.<n>] …`, add labels (type, area, stream, priority, phase, size), a milestone, acceptance criteria and **Blocked by**.
- **Out-of-scope ideas:** add them to the `Future` milestone with the `future` label.
