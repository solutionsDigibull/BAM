# BAM

SBAM (Skills Build Acceleration Manager), the build-time layer of BuLLM. Start with
[`docs/README.md`](docs/README.md) and [`AGENTS.md`](AGENTS.md).

## Layout

| Path | What |
|---|---|
| `client/` | Next.js App Router + Refine (TypeScript strict, pnpm, Node 22) |
| `server/` | One uv workspace (Python 3.12): `api/sbam_api`, `workers/sbam_workers`, `contracts/sbam_contracts`, `templates/`, `tests/` |
| `compose/` | Docker Compose stack |
| `docs/` | Specs, phases, ADRs, runbooks |

## Prerequisites

- git, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node 22 (`client/.nvmrc`), pnpm (`corepack enable pnpm`)
- Docker Desktop (WSL2 backend on Windows) for the Compose stack
- Windows: use Git Bash or WSL2 for `make`, and run `git config --global core.longpaths true`

Run `make doctor` to check them.

## Quickstart

```bash
make install     # uv sync --locked + pnpm install --frozen-lockfile
make check       # lint, types, tests, client build
uvx pre-commit install   # ruff, black, mypy, eslint, prettier, gitleaks on every commit
pnpm --dir client dev    # http://localhost:3000
```

Lockfiles (`server/uv.lock`, `client/pnpm-lock.yaml`) are committed. Add dependencies with `uv add` /
`pnpm --dir client add`, never by editing lockfiles.
