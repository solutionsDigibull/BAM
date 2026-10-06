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

```text
profile core (always):
  sbam-web · sbam-api · api-migrate (one-off) · tusd · postgres (sbam, temporal, temporal_visibility, keycloak, langfuse)
  keycloak (+ realm import) · temporal · temporal-ui · bifrost · clamav (clamd + freshclam)
  worker-ingest · worker-knowledge · worker-llm · worker-build · worker-test · worker-certify
  sbam-mock-api
  volumes: pgdata, storage (uploads, derived, okf, code, tests, exports), cognee-data, clamav-db
profile obs:        otel-collector · grafana-lgtm (Loki, Tempo, Prometheus, Grafana) · langfuse-web · langfuse-worker
                    · clickhouse · redis · minio (internal, only for Langfuse blob storage)
profile analytics:  posthog (self-hosted; heavier — run on dev/staging VM, optional on 64 GB laptops)
profile sandbox-local: local-docker sandbox runner (non-certifying) for laptops without the KVM host
```

| Service                | Port (local)       | Healthcheck                    | Depends on                            |
| ---------------------- | ------------------ | ------------------------------ | ------------------------------------- |
| sbam-web               | 3000               | HTTP`/api/health`            | sbam-api, keycloak                    |
| sbam-api               | 8000               | `/health/ready`              | postgres, temporal, keycloak, storage |
| tusd                   | 1080               | HTTP                           | sbam-api (hooks)                      |
| postgres               | 5432               | `pg_isready`                 | —                                    |
| keycloak               | 8080               | `/health/ready`              | postgres                              |
| temporal / temporal-ui | 7233 / 8233        | gRPC health / HTTP             | postgres                              |
| bifrost                | 8081               | HTTP                           | —                                    |
| clamav                 | 3310               | `clamdscan --ping`           | —                                    |
| workers                | —                 | heartbeat +`/health` sidecar | temporal, postgres, bifrost           |
| langfuse               | 3001               | HTTP                           | postgres, clickhouse, redis, minio    |
| grafana-lgtm           | 3002 / 4317 / 4318 | HTTP                           | —                                    |
| posthog                | 8010               | HTTP                           | own deps                              |

Rules: `.env.example` committed, real `.env` and secrets never committed; `depends_on: service_healthy`; `api-migrate` before `sbam-api`; resource limits on workers; named volumes; `make reset` is the only documented destructive command. Indicative local footprint: `core` ≈ 10–12 GB RAM, `+obs` ≈ +5 GB, `+analytics` ≈ +6 GB, which fits 32 GB laptops (analytics optional) and 64 GB laptops (all profiles).

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

Nightly `pg_dump` of all databases + snapshots of the `storage` and `cognee-data` volumes → off-host backup location (S3 later). Retention 30 days *(proposed)*. A monthly restore drill re-verifies all frozen manifest hashes.

---
[← §19](19-testing-strategy.md)   [Design index](README.md)
