> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §20. Docker and environments


## 20.1 Environments (C-39)

| Env          | Where                                                      | Compose file                                 | Data                       | Access                                      |
| ------------ | ---------------------------------------------------------- | -------------------------------------------- | -------------------------- | ------------------------------------------- |
| local        | Developer laptop (32–64 GB, Docker Desktop/WSL2 or Linux) | `compose.yaml` + `compose.override.yaml` | Seed + fixtures            | localhost                                   |
| dev          | Shared VM                                                  | `compose.yaml` + `compose.dev.yaml`      | Seed                       | Netbird                                     |
| staging      | VM (prod-like)                                             | `compose.yaml` + `compose.staging.yaml`  | Non-sensitive real samples | Netbird + proxy                             |
| production   | VM(s)                                                      | `compose.yaml` + `compose.prod.yaml`     | Real                       | Public proxy (portals only) + Netbird (ops) |
| sandbox host | Linux/KVM server                                           | `deploy/sandbox-host/`                     | Ephemeral                  | Netbird only                                |

AWS (EC2 + S3 + RDS optional) is the future target. The storage adapter and Compose layout are designed so the move is a configuration change.

## 20.2 Images

| Image              | Base*(pinned digest)*                                                            | Contents                                                                                                                                                                                                          |
| ------------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sbam-web`       | `node:22-alpine` build → `node:22-alpine` runtime (Next.js standalone output) | Next.js app, non-root                                                                                                                                                                                             |
| `sbam-api`       | `python:3.12-slim` multi-stage (uv)                                              | FastAPI + Alembic;`migrate` entry                                                                                                                                                                               |
| `sbam-worker`    | `python:3.12-slim`                                                               | All worker code;`--queues` selects queues; parsing libs, Cognee, openpyxl, docx/pdf libs                                                                                                                        |
| `sbam-toolchain` | `python:3.12-slim`                                                               | Pinned Ruff, Black, MyPy, Bandit; used by the build worker and CI                                                                                                                                                 |
| `sbam-promptfoo` | `node:22-slim`                                                                   | Pinned Promptfoo CLI                                                                                                                                                                                              |
| `skill-runtime`  | `python:3.12-slim` + PostgreSQL 16 server binaries                               | FastMCP + every allowed library (no DuckDB), SBAM loop harness, MCP client; ephemeral PostgreSQL for schema/state/handoff tests; generates`runtime_registry` JSON at build; source for the CubeSandbox template |
| `sbam-mock-api`  | per tool chosen                                                                    | Serves mock APIs for sandbox tests                                                                                                                                                                                |

Standards: multi-stage builds, non-root, pinned digests, `HEALTHCHECK`, no secrets in layers, OCI labels, hadolint clean, Trivy (PRs fail on CRITICAL; HIGH reported and must be resolved or accepted before release — [PLAN §28](../plan/06-mvp-dod-delivery-release.md)), Syft SBOM.

## 20.3 Compose services and profiles

**The whole application runs in Docker from day one.** `make up` starts the complete `core` profile on a
laptop. Services whose feature code does not exist yet run as **stubs** (§20.3.6) that keep their final name,
port and healthcheck, so nothing downstream changes when the real implementation lands.

```text
profile core (always):
  sbam-web · sbam-api · api-migrate (one-off) · tusd · postgres (sbam, temporal, temporal_visibility, keycloak, langfuse)
  keycloak (+ realm import) · temporal (+ temporal-schema, temporal-namespace one-offs) · temporal-ui · bifrost
  clamav (clamd + freshclam) · storage-init (one-off)
  worker-ingest · worker-knowledge · worker-llm · worker-build · worker-test · worker-certify
  sbam-mock-api
  volumes: pgdata, storage (uploads, derived, okf, code, tests, exports), cognee_data, clamav_db
profile obs:        otel-collector · grafana-lgtm (Loki, Tempo, Prometheus, Grafana) · langfuse-web · langfuse-worker
                    · clickhouse · redis · minio (internal, only for Langfuse blob storage)
profile analytics:  posthog (self-hosted; heavier — run on dev/staging VM, optional on 64 GB laptops)
profile sandbox-local: local-docker sandbox runner (non-certifying) for laptops without the KVM host
```

Each optional profile is its own file, `compose/compose.<profile>.yaml`, added by `make up PROFILES=obs,...`
(E1.11). They aren't `profiles:` entries in `compose.yaml` because Compose interpolates every service, active
or not, so profile-only required secrets would break plain `make up` for an `.env` that predates them.

**Redis is not an SBAM dependency.** It exists only inside the `obs` profile because Langfuse needs it. SBAM state
(idempotency keys, runs, caches) lives in PostgreSQL. Don't add Redis to `core` without an ADR.

| Service                | Host port (default, `.env` var) | Healthcheck                         | Depends on (healthy)                  |
| ---------------------- | ------------------------------- | ----------------------------------- | ------------------------------------- |
| sbam-web               | 3000 `WEB_PORT`                 | HTTP `/api/health`                  | sbam-api, keycloak                    |
| sbam-api               | 8000 `API_PORT`                 | HTTP `/health/ready`                | api-migrate (completed), postgres, temporal, keycloak, storage-init |
| api-migrate            | —                               | one-off, exit 0                     | postgres                              |
| tusd                   | 1080 `TUSD_PORT`                | HTTP `/files/` (OPTIONS)            | sbam-api (hooks)                      |
| postgres               | 5432 `POSTGRES_PORT`            | `pg_isready`                        | —                                     |
| keycloak               | 8080 `KEYCLOAK_PORT`            | HTTP `:9000/health/ready`           | postgres                              |
| temporal               | 7233 `TEMPORAL_PORT`            | `temporal operator cluster health`  | temporal-schema (completed)           |
| temporal-ui            | 8233 `TEMPORAL_UI_PORT`         | HTTP                                | temporal                              |
| bifrost                | 8081 `BIFROST_PORT` (container 8080) | HTTP `/health`                 | —                                     |
| clamav                 | not published (internal only)   | `clamdcheck.sh`                     | —                                     |
| workers (×6)           | not published                   | heartbeat file younger than 30 s    | temporal, postgres, bifrost           |
| sbam-mock-api          | not published                   | HTTP `/health`                      | —                                     |
| langfuse (obs)         | 3001                            | HTTP                                | postgres, clickhouse, redis, minio    |
| grafana-lgtm (obs)     | 3002 / 4317 / 4318              | HTTP                                | —                                     |
| posthog (analytics)    | 8010                            | HTTP                                | own deps                              |

Rules: `.env.example` committed, real `.env` and secrets never committed; `depends_on: service_healthy`;
`api-migrate` before `sbam-api`; resource limits on workers; named volumes; **every published port binds to
`127.0.0.1`** and is overridable in `.env` (ports such as 8000 or 8080 are often taken); `make reset` is the only
documented destructive command. Indicative local footprint: `core` ≈ 10–12 GB RAM, `+obs` ≈ +5 GB,
`+analytics` ≈ +6 GB. Give Docker Desktop at least 12 GB for `core` (it fits a 16 GB Docker VM; 32 GB laptops
recommended, analytics optional on 64 GB).

### 20.3.1 Pinned versions

Every image is pinned to the version below **and** its `@sha256` digest in `compose.yaml` / Dockerfiles. A version
bump is a reviewed PR.

| Service | Image | Version |
|---|---|---|
| postgres | `postgres` | `16.15-alpine` |
| keycloak | `quay.io/keycloak/keycloak` | `26.8.0` |
| temporal | `temporalio/server` | `1.31.3` |
| temporal-schema / temporal-namespace | `temporalio/admin-tools` | `1.31.3` |
| temporal-ui | `temporalio/ui` | `2.55.0` |
| tusd | `tusproject/tusd` | `v2.10.1` |
| clamav | `clamav/clamav` | `1.5.4` |
| bifrost | `maximhq/bifrost` | `v2.2.6` |
| sbam-web base | `node` | `22.23-alpine` (build) / `22.23-alpine` (runtime) |
| sbam-api / worker / toolchain / skill-runtime base | `python` | `3.12.15-slim` |
| sbam-promptfoo base | `node` | `22.23-slim` |
| otel-collector (obs) | `grafana/otel-lgtm` | `0.35.0` |
| langfuse (obs) | `langfuse/langfuse` | `3.225.11` |

`temporalio/auto-setup` is deprecated upstream (last release 1.29); SBAM uses `temporalio/server` plus an
`admin-tools` one-off job that creates and updates the schema and the namespace.

### 20.3.2 Environment variables

All Compose configuration comes from `compose/.env` (copied from the committed `compose/.env.example`).
`make up` fails fast and names any missing required variable. Secrets never go into images, and **workers never
get an `env_file`**. Each worker variable is passed one by one, so the OpenRouter key can't leak into them (ADR 0001).

| Variable | Used by | Required | Notes |
|---|---|---|---|
| `WEB_PORT`, `API_PORT`, `KEYCLOAK_PORT`, `TEMPORAL_PORT`, `TEMPORAL_UI_PORT`, `TUSD_PORT`, `BIFROST_PORT`, `POSTGRES_PORT` | host port mapping | no | defaults in the table above |
| `POSTGRES_PASSWORD` | postgres superuser | yes | local only |
| `SBAM_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `LANGFUSE_DB_PASSWORD` | one login role per database | yes | created by the init SQL |
| `KEYCLOAK_ADMIN_PASSWORD` | keycloak bootstrap admin | yes | admin console only on localhost / Netbird |
| `SEED_USER_PASSWORD` | dev realm seed users (local/dev only) | yes | never set in staging/prod |
| `OPENROUTER_API_KEY` | **bifrost only** | no | empty → Bifrost serves the mock provider (`LLM_PROVIDER=mock`) |
| `LLM_PROVIDER` | workers | no | `mock` (default, CI) or `bifrost` |
| `TUSD_HOOK_SECRET` | tusd → sbam-api hooks | yes | |
| `SBAM_DEV_AUTH` | sbam-web | no | `1` enables the dev role picker until Keycloak sign-in (#51); ignored in production builds |
| `MAX_BODY_BYTES`, `LOG_LEVEL` | sbam-api | no | defaults 10 MiB, `INFO` |

Compose builds the API's own settings from these. Inside the stack:

| sbam-api variable | Value inside Compose |
|---|---|
| `DATABASE_URL` | `postgresql://sbam:${SBAM_DB_PASSWORD}@postgres:5432/sbam` |
| `STORAGE_ROOT` | `/data/storage` |
| `TEMPORAL_HOST` | `temporal:7233` |
| `KEYCLOAK_ISSUER` | `http://keycloak:8080/realms/bullm` |
| `KEYCLOAK_AUDIENCE` | `sbam-api` |

`server/.env` is only for running the API on the host against the Compose services. It uses the same variables,
pointed at `localhost` and the published ports.

### 20.3.3 Networks

| Network | Internet | Members | Why |
|---|---|---|---|
| `sbam_edge` | yes | services with a published port (web, api, keycloak, temporal-ui, tusd) and bifrost, clamav (signature updates) | Docker can't publish ports from an internal-only network |
| `sbam_internal` | **no** (`internal: true`) | every service | service-to-service traffic |

Workers, including `worker-knowledge` (Cognee runs as a library inside it, ADR 0001), sit only on `sbam_internal`.
They can reach postgres, temporal and bifrost, but they have **no internet route**. Only bifrost calls OpenRouter.

### 20.3.4 Init jobs (one-off, idempotent)

| Job | What it does | Idempotency |
|---|---|---|
| postgres `/docker-entrypoint-initdb.d/init.sql` | creates the roles and databases `sbam`, `temporal`, `temporal_visibility`, `keycloak`, `langfuse` | runs only on an empty `pgdata` volume |
| `temporal-schema` | `temporal-sql-tool` setup + update for `temporal` and `temporal_visibility` | `setup-schema` / `update-schema` are re-runnable |
| `temporal-namespace` | creates namespace `sbam` (retention 7 days) if missing | checks first |
| keycloak `--import-realm` | imports `compose/keycloak/realm-bullm-dev.json` (clients `sbam-admin`, `sbam-fde`, roles, seed users) | skips an existing realm |
| `storage-init` | creates `uploads`, `derived`, `okf`, `code`, `tests`, `exports` on the `storage` volume, owned by uid 10001 | `mkdir -p` |
| `api-migrate` | `alembic upgrade head` (stub: exits 0 until #36) | Alembic is re-runnable |
| clamav | freshclam on start (first download takes more than 2 min; healthcheck `start_period` covers it) | built in |

### 20.3.5 Local development mode

`compose.override.yaml` is loaded automatically by `make up` on laptops. It bind-mounts the source, so code
changes don't need image rebuilds:

- **sbam-api** uses the `dev` image target, with `server/` mounted and `uvicorn … --reload`.
- **sbam-web** uses the `dev` image target, with `client/` mounted (its `node_modules` stays in a named volume) and `next dev`.
- **workers** use the `dev` image target, with `server/` mounted and restarted on change.

Dev, staging and production use `compose.<env>.yaml` with the `prod` image targets and no source mounts (#142).

### 20.3.6 Stub policy and ownership

`make up` must always bring the full `core` profile up healthy. Until its feature issue lands, a service runs as a
stub with its **final** service name, port, network and healthcheck. The owning issue replaces only its own
service block in `compose.yaml` (and its config folder), never other blocks.

| Service | Stub until then | Replaced by |
|---|---|---|
| keycloak realm | dev realm `bullm`: 2 clients, 3 roles, seed users | #47 E2.1 (hardened realm, tenant mapper, Organizations) |
| bifrost | Bifrost with the mock provider, no aliases yet | #46 E1.15 (six `sbam-*` aliases, fallback, mock fixtures) |
| tusd | tusd with file store on `storage`, no hooks | #61 E4.2 (pre-create / post-finish hooks) |
| clamav | real clamd + freshclam; not yet called | #62 E4.3 (scan wiring) |
| workers ×6 | `sbam-worker` image, heartbeat loop per queue | #40 E1.7 (bootstrap `--queues`) |
| api-migrate | exits 0 ("no migrations yet") | #36 E1.5 (Alembic) |
| sbam-mock-api | health-only HTTP server | #126 E13.5 |
| otel-collector / langfuse (obs) | real (`compose.obs.yaml`, #42 E1.11) | — |
| posthog (analytics) | busybox health endpoint on 8010 | #141 E16.2 / #142 E16.3 |
| sandbox-runner (sandbox-local) | hardened idle container on internal `sbam_sandbox` (peer: `sbam-mock-api` only), `SANDBOX_MODE=LOCAL_DOCKER_NONCERT` | #123 E13.2 (adapter, `EGRESS_BLOCKED`, non-certifying runs) |

### 20.3.7 Running it

```bash
cp compose/.env.example compose/.env   # set the passwords
make up            # core profile, healthy in ~5–10 min on first run (image pulls + ClamAV signatures)
make ps            # docker compose ps: every service "healthy"
make logs s=sbam-api
make up PROFILES=obs,sandbox-local   # core + optional profiles (obs, analytics, sandbox-local)
make test-int      # integration tests against the running stack (TD §19)
make down          # stop, keep data
make reset CONFIRM=yes   # stop and delete all volumes (the only destructive command)
```

URLs: web `http://localhost:3000`, API docs `http://localhost:8000/api/v1/docs`, Keycloak
`http://localhost:8080`, Temporal UI `http://localhost:8233`. With `obs`: Grafana `http://localhost:3002`
(user `admin`), Langfuse `http://localhost:3001`, OTLP `localhost:4317` (gRPC) / `4318` (HTTP).

## 20.4 CI/CD (GitHub Actions)

```text
pull_request: lint (ruff, eslint) → types (mypy, tsc) → unit → API → contract drift → Temporal replay
              → hadolint → build images (cache) → Trivy → SBOM → compose smoke (core) → Playwright smoke
main:         same → push images to GHCR (:sha, :main) → deploy dev automatically
release tag:  promote same digests → deploy staging (auto) → E2E suite → manual approval → deploy production
nightly:      rebuild + rescan images; full E2E on staging; frozen-manifest hash verification job
```

Deploy = SSH over Netbird → `docker compose pull` (by digest) → `api-migrate` → `up -d` → health check → automatic rollback to previous digests on failure.

## 20.5 Netbird (C-47)

One Netbird network with groups: `dev-laptops`, `vm-dev`, `vm-staging`, `vm-prod`, `sandbox-host`. Policies: laptops → dev/staging ops ports; workers → sandbox host API; production ops only for the platform group. No database, Temporal or admin UI is exposed publicly.

## 20.6 Backup

Nightly `pg_dump` of all databases + snapshots of the `storage` and `cognee_data` volumes → off-host backup location (S3 later). Retention 30 days *(proposed)*. A monthly restore drill re-verifies all frozen manifest hashes.

---
[← §19](19-testing-strategy.md)   [Design index](README.md)
