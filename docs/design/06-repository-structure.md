> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §6. Repository structure


Repository: **`solutionsDigibull/BAM`** (private). Its top-level folders `client/`, `server/`, `compose/` and `docs/` already exist and are kept. Everything SBAM goes inside them as follows:

```text
BAM/
├── client/                              Next.js (App Router) + Refine — Admin and FDE portals
│   ├── src/app/
│   │   ├── (auth)/login-admin, (auth)/login-fde
│   │   ├── (admin)/projects, masters, users, registry, tenants
│   │   └── (fde)/projects/[projectId]/   overview, documents, knowledge, sow,
│   │          workflows/[workflowId]/    isatvon, build, manifests, code, certification, versions
│   ├── src/providers/ (refine data provider, auth, access control)
│   ├── src/components/, src/lib/
│   ├── src/contracts/                   GENERATED TypeScript types — never edit by hand
│   └── e2e/                             Playwright scenarios E01–E20
├── server/
│   ├── pyproject.toml, uv.lock          one Python workspace (api + workers + contracts)
│   ├── contracts/sbam_contracts/        Pydantic models: isatvon14, manifest, masters, sow, api errors + gen scripts
│   ├── api/sbam_api/                    FastAPI modular monolith
│   │   ├── core/ (settings, errors, logging, telemetry, security)
│   │   ├── db/, migrations/ (Alembic)
│   │   ├── storage/ (StorageBackend, LocalDiskBackend, S3Backend stub)
│   │   ├── tenancy/, auth/, audit/, notifications/
│   │   ├── projects/, masters/, registry/, documents/, knowledge/, sow/
│   │   ├── workflows/, isatvon/, manifests/, build/, tests/, certification/, versions/, exports/
│   │   └── temporal/ (client, signals, run mirror)
│   ├── workers/sbam_workers/
│   │   ├── bootstrap.py (--queues)
│   │   ├── pipeline/ (ProjectPipeline, SubSkillBuild)
│   │   ├── ingest/, knowledge/ (cognee, okf), llm/ (bifrost client, prompts, agents)
│   │   ├── sow/, isatvon/, build/ (manifest, schema_sql, codegen, correction, ast_gate, static_gate)
│   │   ├── test/ (testgen, harness, promptfoo, sandbox adapters, mocks), certify/, exports/
│   ├── templates/fastmcp-loopy/<version>/  Jinja2 skeletons + golden snapshots
│   └── tests/ (unit, api, contract, temporal, security, perf, llm-golden, fixtures)
├── compose/
│   ├── compose.yaml, compose.override.yaml, compose.<env>.yaml, .env.example
│   ├── docker/ (Dockerfiles: web, api, worker, toolchain, promptfoo, skill-runtime)
│   ├── keycloak/ (realm export), bifrost/, clamav/, otel/, grafana/
│   ├── sandbox-host/ (CubeSandbox install, Netbird), backup/
├── docs/                                specs (this document), developer guides, ADRs, spikes, runbooks
├── Makefile                             make up | down | logs | seed | test | e2e | reset
└── .github/workflows/ (ci.yml, images.yml, deploy-*.yml)
```

Where issues say `apps/web`, `services/api`, `services/workers`, `packages/contracts`, `templates/` or `deploy/`, read `client/`, `server/api`, `server/workers`, `server/contracts`, `server/templates` and `compose/` respectively.

---
[← §5](05-component-architecture.md)   [Design index](README.md)   [§7 →](07-database-design.md)
