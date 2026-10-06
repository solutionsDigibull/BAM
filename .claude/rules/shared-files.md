---
paths:
  - "server/contracts/**"
  - "client/src/contracts/**"
  - "server/api/sbam_api/migrations/**"
  - "server/api/sbam_api/main.py"
  - "server/api/sbam_api/core/**"
  - "server/api/sbam_api/audit/**"
  - "server/workers/sbam_workers/pipeline/**"
  - "server/workers/sbam_workers/bootstrap.py"
  - "server/pyproject.toml"
  - "server/uv.lock"
  - "client/package.json"
  - "client/pnpm-lock.yaml"
  - "client/src/app/layout.tsx"
  - "client/src/providers/**"
  - "compose/compose*.yaml"
  - "compose/.env.example"
  - "Makefile"
  - ".github/**"
---

# Shared "hot" files

Many parallel branches touch these files, so conflicts here are the most expensive. Each one has an owning
lane (AGENTS.md table, docs/TEAM_PLAN.md §G). If you don't own the file, keep the diff minimal and request
the owner's review.

- Make the smallest possible diff. Don't reorder, reformat or sort existing entries.
- **Contracts** (`server/contracts`, owner D3): change them in their own small PR before the code that uses
  them, and list every caller you updated. Then run `make contracts`. Never hand-edit `client/src/contracts/`;
  it is generated, and CI fails on drift.
- **Migrations** (owner D1 for the base): one new Alembic revision per PR, generated after rebasing on
  `upstream/main` and right before merge. Never edit a migration that is already on `main`. If another PR
  merged a migration first, delete yours and regenerate it so there is a single head. Never use `alembic merge`.
  Touch only your own module's tables.
- **`main.py` / `core/`** (owner D1): routers are auto-discovered, so a feature PR never registers a
  router here. A change to core needs D1's review.
- **ProjectPipeline / `bootstrap.py`** (owner D3): stages are stub activities with fixed names and Pydantic
  in/out models. Implement your activity in your own module with the registry decorator. Only D3 changes
  workflow wiring, always with `workflow.patched` and a replay test.
- **Dependencies:** add only what the issue needs, with `uv add` / `pnpm --dir client add`. Never hand-edit
  `uv.lock` or `pnpm-lock.yaml`. On a rebase conflict, take upstream's lockfile and re-run `uv lock` /
  `pnpm install`.
- **Compose / Makefile / `.env.example`** (owner D2): edit only your own service block or target. New
  env vars go in `.env.example` with a comment and a safe default. Environment overlays go in
  `compose.<env>.yaml`.
- **Client layout, nav, providers** (owner D1): every route and nav entry already exists as an empty page.
  Fill in your page folder. Don't restructure the layout.
- **CI (`.github/`)** (owner D1): change it only when the issue is about CI. New jobs get their own workflow
  file (e.g. `replay.yml`, `llm-golden.yml`) instead of growing `ci.yml`. Mention the change in the PR title.
- List every hot file touched under "Hot files touched" in the PR description.
