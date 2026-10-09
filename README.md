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

- Docker Desktop (WSL2 backend on Windows) or Docker Engine, with **at least 12 GB of memory for Docker**
- git and make (Windows: use Git Bash or WSL2, and run `git config --global core.longpaths true`)
- For host-side tests and editors: [uv](https://docs.astral.sh/uv/getting-started/installation/), Node 22 (`client/.nvmrc`), pnpm (`corepack enable pnpm`)

Run `make doctor` to check them.

## Run everything in Docker (running in 15 minutes)

The whole application (web, API, six workers, Postgres, Keycloak, Temporal, Bifrost, ClamAV, tusd) runs with
Docker Compose ([TD §20](docs/design/20-docker-and-environments.md)). Services whose feature isn't built yet
run as stubs with their final name and port (TD §20.3.6).

1. **Install** Docker (12 GB+ memory for Docker), git and make:
   - **Windows:** Docker Desktop with the WSL2 backend; run everything below in **Git Bash** or a WSL2
     shell. Get make with `winget install ezwinports.make` (then reopen the shell), or work inside WSL2.
   - **macOS:** Docker Desktop (Settings → Resources → Memory ≥ 12 GB); make ships with the Xcode CLT
     (`xcode-select --install`).
   - **Linux:** Docker Engine + the compose plugin, and `make` from your package manager.
2. **Configure:** `cp compose/.env.example compose/.env`, then replace every `change-me-…` password. If a
   port is taken (8000 and 8080 often are), change it there, e.g. `API_PORT=8917`.
3. **Start:** `make up`. The first run pulls and builds images and downloads ClamAV signatures (~5–10 min);
   it returns when every service is healthy. Missing variables stop it with the variable's name.
4. **Check:** `make ps` shows every service `healthy` (one-off init jobs show `exited (0)`).

| URL | What |
|---|---|
| http://localhost:3000/login-fde | web (dev sign-in picker) |
| http://localhost:8000/api/v1/docs | API docs (`/health/ready` = all dependencies ok) |
| http://localhost:8080 | Keycloak admin (`admin` / `KEYCLOAK_ADMIN_PASSWORD`), realm `bullm` |
| http://localhost:8233 | Temporal UI, namespace `sbam` |

| Command | What |
|---|---|
| `make up` / `make down` | start (or update) / stop; data is kept in named volumes |
| `make ps`, `make logs s=sbam-api` | status, follow one service's logs |
| `make test-int` | integration tests against the running stack (`pytest -m integration`) |
| `make e2e` | Playwright end-to-end tests |
| `make seed` | stub until #54/#55 |
| `make reset CONFIRM=yes` | **deletes all stack data** (the only destructive command) |

All ports bind to `127.0.0.1` only. `compose.override.yaml` (loaded by `make up`) runs the API and web in dev
mode with your `server/` and `client/` mounted, so code changes reload without rebuilding images. See
[DEVELOPER_GUIDE §6](docs/DEVELOPER_GUIDE.md#6-local-development) for more.

## Develop on the host

```bash
make install     # uv sync --locked + pnpm install --frozen-lockfile
make check       # lint, types, tests, client build
uvx pre-commit install   # ruff, black, mypy, eslint, prettier, gitleaks on every commit
pnpm --dir client dev    # http://localhost:3000
```

Until Keycloak sign-in lands (#51), start the client with `SBAM_DEV_AUTH=1` (for example in
`client/.env.local`) to get a dev-only role picker on `/login-admin` and `/login-fde`. It is ignored in
production builds. To sign in through Keycloak instead, set `KEYCLOAK_ISSUER`, `AUTH_SECRET` and
`SBAM_API_URL` for the client (the spike realm in [`spikes/identity`](spikes/identity/README.md) works
until #47 hardens the Compose realm). E2E tests: `pnpm --dir client exec playwright install chromium` once, then
`pnpm --dir client e2e`.

### Run the API

The API reads its settings from the environment or `server/.env` (gitignored). It refuses to start
if any of these are missing:

| Variable | Example |
|---|---|
| `DATABASE_URL` | `postgresql://sbam:…@localhost:5432/sbam` |
| `STORAGE_ROOT` | `./data/storage` (must exist and be writable) |
| `TEMPORAL_HOST` | `localhost:7233` |
| `KEYCLOAK_ISSUER` | `http://localhost:8080/realms/bullm` |
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
