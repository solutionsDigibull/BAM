> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 1 — Repository Foundation and Containerized Dev Stack

| Epic | Milestone | Stories |
|---|---|---|
| [#2](https://github.com/solutionsDigibull/BAM/issues/2) `E1` | M0 Foundation | 15 |


**Goal:** Create the fresh monorepo, the full Docker Compose stack and CI, so any developer can run SBAM locally on day one.

**Owner:** Dev A (platform) · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 0
**Design references:** [TD §6](../design/06-repository-structure.md) (repo structure), [TD §20](../design/20-docker-and-environments.md) (Docker & environments).

### Repository

- [ ] Repository layout in `solutionsDigibull/BAM` (`client/`, `server/{api,workers,contracts,templates,tests}`, `compose/`, `docs/`) per [TD §6](../design/06-repository-structure.md).
- [ ] Python tooling (uv, Ruff, Black, MyPy strict, pytest) and TS tooling (pnpm, ESLint, Prettier, Vitest, Playwright).
- [ ] Contract package: Pydantic → JSON Schema → TypeScript generation script; CI fails on drift.
- [ ] ADR folder with the Phase 0 decisions.

### Backend skeleton

- [ ] FastAPI app factory, settings (Pydantic settings, env-driven), modular routers per domain module.
- [ ] SQLAlchemy 2 + Alembic baseline (`sbam` schema), conventions (UUIDv7, `tenant_id` + `project_id`, `row_version`, `deleted_at`).
- [ ] Problem+json error handler, correlation-ID middleware, structured JSON logging, OTEL instrumentation.
- [ ] `/health/live` and `/health/ready` (DB, storage, Temporal, Keycloak JWKS).
- [ ] `StorageBackend` interface + `LocalDiskBackend` (content-addressed, atomic writes) + S3 backend stub.
- [ ] Temporal client + worker bootstrap with `--queues` selection and OTEL interceptor.

### Frontend skeleton

- [ ] Next.js App Router app with `(admin)` and `(fde)` route groups, Refine provider, layout and navigation per portal.
- [ ] Auth.js Keycloak provider (session, token refresh), API client with problem+json mapping, Refine data provider.
- [ ] Shared UI kit: StatusBadge, StepTimeline, EmptyState, ErrorPanel, HashChip, NotificationBell.

### Docker and CI

- [ ] Dockerfiles: `sbam-web`, `sbam-api`, `sbam-worker`, `sbam-toolchain`, `sbam-promptfoo`, `skill-runtime` ([TD §20.2](../design/20-docker-and-environments.md)).
- [ ] `compose.yaml` with profiles `core`, `obs`, `analytics`, `sandbox-local` and `compose.override.yaml` for hot reload ([TD §20.3](../design/20-docker-and-environments.md)).
- [ ] Init jobs: Postgres databases (sbam, temporal, temporal_visibility, keycloak, langfuse), Keycloak realm import, Temporal namespace, storage volume layout, ClamAV signature update.
- [ ] `.env.example`, Makefile (`up`, `down`, `logs`, `seed`, `test`, `e2e`, `reset`), README "running in 15 minutes".
- [ ] GitHub Actions: lint → types → unit → API tests → hadolint → build images → Trivy → SBOM → Compose smoke test → push to GHCR on `main`.

### Exit Criteria

- [ ] `make up` brings up the `core` profile healthy on Windows (Docker Desktop, WSL2), macOS and Linux with 32 GB RAM.
- [ ] A sample Temporal workflow runs on every worker queue and is visible in Temporal UI.
- [ ] CI runs on every PR and blocks on lint, type, test, image-scan (CRITICAL) and contract-drift failures.
- [ ] Generated TS types come only from Pydantic models.

## Design pages to read

- [TD §4. System flow (transition detail)](../design/04-system-flow.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §6. Repository structure](../design/06-repository-structure.md)
- [TD §7. Database design](../design/07-database-design.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §13. Temporal design](../design/13-temporal-design.md)
- [TD §14. State machines](../design/14-state-machines.md)
- [TD §17. Observability and notifications](../design/17-observability-and-notifications.md)
- [TD §20. Docker and environments](../design/20-docker-and-environments.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#32](https://github.com/solutionsDigibull/BAM/issues/32) | `E1.1` | Monorepo scaffold and tooling | A Platform | M | P0 | M0 | — |
| [#33](https://github.com/solutionsDigibull/BAM/issues/33) | `E1.2` | Contracts package: Pydantic → JSON Schema → TypeScript generation + drift check | C Build | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32), [#19](https://github.com/solutionsDigibull/BAM/issues/19), [#20](https://github.com/solutionsDigibull/BAM/issues/20), [#21](https://github.com/solutionsDigibull/BAM/issues/21) |
| [#34](https://github.com/solutionsDigibull/BAM/issues/34) | `E1.3` | FastAPI core: app factory, settings, errors, correlation IDs, logging, health | A Platform | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32), [#24](https://github.com/solutionsDigibull/BAM/issues/24) |
| [#35](https://github.com/solutionsDigibull/BAM/issues/35) | `E1.4` | OpenTelemetry instrumentation baseline | A Platform | S | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#36](https://github.com/solutionsDigibull/BAM/issues/36) | `E1.5` | Database: SQLAlchemy 2 + Alembic baseline + tenant-scoped repository base | A Platform | M | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#37](https://github.com/solutionsDigibull/BAM/issues/37) | `E1.6` | StorageBackend interface + LocalDiskBackend + S3 stub | A Platform | S | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#40](https://github.com/solutionsDigibull/BAM/issues/40) | `E1.7` | Temporal client, worker bootstrap (--queues), run mirror | C Build | M | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34), [#39](https://github.com/solutionsDigibull/BAM/issues/39) |
| [#41](https://github.com/solutionsDigibull/BAM/issues/41) | `E1.8` | State-machine service + audit writer | C Build | M | P0 | M0 | [#36](https://github.com/solutionsDigibull/BAM/issues/36), [#23](https://github.com/solutionsDigibull/BAM/issues/23) |
| [#38](https://github.com/solutionsDigibull/BAM/issues/38) | `E1.9` | Dockerfiles: web, api, worker, toolchain, promptfoo, skill-runtime base, mock-api | A Platform | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32) |
| [#39](https://github.com/solutionsDigibull/BAM/issues/39) | `E1.10` | Docker Compose core profile + init jobs + Makefile + README | A Platform | M | P0 | M0 | [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#42](https://github.com/solutionsDigibull/BAM/issues/42) | `E1.11` | Compose profiles obs, analytics, sandbox-local | A Platform | S | P1 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39) |
| [#43](https://github.com/solutionsDigibull/BAM/issues/43) | `E1.12` | GitHub Actions CI pipeline | A Platform | M | P0 | M0 | [#38](https://github.com/solutionsDigibull/BAM/issues/38), [#33](https://github.com/solutionsDigibull/BAM/issues/33) |
| [#44](https://github.com/solutionsDigibull/BAM/issues/44) | `E1.13` | Next.js + Refine app shell, (admin)/(fde) route groups, UI kit | D Frontend | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32) |
| [#45](https://github.com/solutionsDigibull/BAM/issues/45) | `E1.14` | Refine data provider, problem+json mapping, API proxy route | D Frontend | S | P0 | M0 | [#44](https://github.com/solutionsDigibull/BAM/issues/44), [#33](https://github.com/solutionsDigibull/BAM/issues/33) |
| [#46](https://github.com/solutionsDigibull/BAM/issues/46) | `E1.15` | Bifrost container + mock LLM provider for dev/tests | B Knowledge/LLM | S | P0 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39) |

---
[← Phase 0](phase-00-architecture-contracts-and-inputs-baseline.md)   [Phases index](README.md)   [Phase 2 →](phase-02-identity-multi-tenancy-and-rbac.md)
