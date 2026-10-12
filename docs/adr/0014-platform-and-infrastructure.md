# 0014 — Platform and infrastructure

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-25, C-34, C-35, C-36, C-37, C-38, C-39, C-40, C-47, [TD §20](../design/20-docker-and-environments.md), [TD §6](../design/06-repository-structure.md)

## Context

The stack, hosting, environments and developer machines had to be fixed before the foundation work (M0).

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-25 | Dependencies | Everything runs in Docker; SBAM provides a curated runtime with all allowed libraries; PostgreSQL only | QA J1 |
| C-34 | Frontend | Next.js (not Vite) | QA N1 |
| C-35 | Hosting | Local now (Docker Compose on VMs); AWS later | QA N2, N3 |
| C-36 | Temporal | Self-hosted in Docker | QA N4 |
| C-37 | Storage | Local disk volume behind an S3-compatible storage interface; metadata in PostgreSQL; AWS S3 later | QA N5, follow-up |
| C-38 | CI | GitHub Actions (GHCR proposed as registry) | QA N6 |
| C-39 | Environments | local, dev, staging, production | QA N8 |
| C-40 | Dev machines | 32–64 GB RAM; the full stack incl. Langfuse runs locally (PostHog optional profile) | QA N9 |
| C-47 | Netbird*(plan recommendation)* | Private WireGuard mesh linking dev laptops, dev/staging/prod VMs and the CubeSandbox Linux host; nothing exposed publicly except the portal reverse proxy | QA N7 |

## Consequences

- Everything runs in Docker Compose, with Next.js for the frontend and Temporal self-hosted; storage is local disk behind an S3-compatible interface (#37).
- GitHub Actions and GHCR are the CI and registry; Netbird links laptops, VMs and the sandbox host ([prerequisites runbook](../runbooks/prerequisites.md)).

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §20](../design/20-docker-and-environments.md), [TD §6](../design/06-repository-structure.md)
