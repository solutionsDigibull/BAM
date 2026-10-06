# BAM

SBAM (Skills Build Acceleration Manager), the build-time layer of BuLLM. Start with
[`docs/README.md`](docs/README.md) and [`AGENTS.md`](AGENTS.md). Every API endpoint follows
[`docs/api-conventions.md`](docs/api-conventions.md) (problem+json errors, ETag/If-Match, Idempotency-Key,
pagination).

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

Until Keycloak sign-in lands (#51), start the client with `SBAM_DEV_AUTH=1` (for example in
`client/.env.local`) to get a dev-only role picker on `/login-admin` and `/login-fde`. It is ignored in
production builds. E2E tests: `pnpm --dir client exec playwright install chromium` once, then
`pnpm --dir client e2e`.

### Run the API

The API reads its settings from the environment or `server/.env` (gitignored). It refuses to start
if any of these are missing:

| Variable | Example |
|---|---|
| `DATABASE_URL` | `postgresql://sbam:…@localhost:5432/sbam` |
| `STORAGE_ROOT` | `./data/storage` (must exist and be writable) |
| `TEMPORAL_HOST` | `localhost:7233` |
| `KEYCLOAK_ISSUER` | `http://localhost:8080/realms/sbam` |
| `KEYCLOAK_AUDIENCE` | `sbam-api` |
| `MAX_BODY_BYTES`, `LOG_LEVEL` | optional: `10485760`, `INFO` |

```bash
cd server && uv run uvicorn sbam_api.main:create_app --factory --reload --port 8000
curl localhost:8000/health/live     # {"status":"ok"}
curl localhost:8000/health/ready    # 200, or 503 naming the failed dependency
```

API docs are served at `/api/v1/docs`. Logs are JSON lines on stdout.

Lockfiles (`server/uv.lock`, `client/pnpm-lock.yaml`) are committed. Add dependencies with `uv add` /
`pnpm --dir client add`, never by editing lockfiles.
