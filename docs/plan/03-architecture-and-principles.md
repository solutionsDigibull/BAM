> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# PLAN — Architecture Baseline and Engineering Principles

## PLAN §4. Architecture Baseline


### 4.1 Application architecture

```text
             Admin (Admin portal)                         FDE (FDE portal)
                     │ HTTPS (OIDC via Keycloak)                │
                     ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ sbam-web — Next.js (App Router) + Refine                                          │
│  (admin)/  projects · masters · users & roles · tenant settings                   │
│  (fde)/    workflows · upload (Uppy) · processing · knowledge explorer (Xyflow)   │
│            SOW (TipTap) · ISATVON grid (AG Grid) · manifests & code (Monaco)      │
│            build & test dashboard · certification · versions · notifications      │
└───────────────────────────────┬──────────────────────────────────────────────────┘
                                │ REST/JSON (Refine data provider) · tus uploads
                                ▼
┌────────────────────────────────────────────┐        ┌───────────────────────────┐
│ sbam-api — FastAPI (modular monolith)      │◄──────►│ Keycloak (realm, tenants, │
│ tenancy · projects · masters · workflows   │  JWKS  │ admin & fde clients)      │
│ documents · knowledge · sow · isatvon      │        └───────────────────────────┘
│ manifests · build · tests · certification  │
│ versions · exports · notifications · audit │──────► Temporal server + UI (self-hosted)
└───────┬───────────────────┬────────────────┘                 │ task queues
        │                   │                                  ▼
        ▼                   ▼                 ┌────────────────────────────────────────┐
   PostgreSQL        Storage adapter          │ sbam-workers (Temporal)                │
   (sbam, temporal,  (local disk volume;      │  ingest  : ClamAV · parse · LLM OCR    │
    keycloak,         S3 API later)           │  knowledge: Cognee · OKF bundle        │
    langfuse)               ▲                 │  llm     : SOW · ISATVON · test data    │
        ▲                   │                 │  build   : manifest · Jinja2+LLM code · │
        │                   │                 │            AST · Ruff/Black/MyPy/scan   │
        └── evidence ◄──────┴─────────────────│  test    : Promptfoo · CubeSandbox      │
                                              │  certify : certify · version · freeze   │
                                              └──┬─────────┬──────────┬────────────┬───┘
                                                 ▼         ▼          ▼            ▼
                                              Cognee    Bifrost    ClamAV     CubeSandbox host
                                              (default  → Open-    (clamd)    (Linux/KVM, via
                                               stores)    Router              Netbird)
  Observability: OTEL collector → traces/metrics/logs backend · Langfuse (LLM) · PostHog (product/eval)
```

### 4.2 Stack decisions

- **Frontend:** Next.js (App Router, TypeScript) with Refine as the data/CRUD layer (`@refinedev/nextjs-router`), AG Grid (ISATVON workbench), TipTap (SOW editor), Monaco (manifest/code viewer), Uppy + tus (upload), Xyflow (knowledge graph explorer, skill DAG). Two route groups: `(admin)` and `(fde)`. Auth via Auth.js (NextAuth) Keycloak provider *(proposed)*. The backend stays authoritative.
- **Backend:** FastAPI (Python 3.12), Pydantic v2 contracts, Pydantic AI for structured LLM output, SQLAlchemy 2 + Alembic.
- **Database:** PostgreSQL 16, a single engine for SBAM, Temporal, Keycloak and Langfuse (separate databases). No DuckDB.
- **Storage:** `StorageBackend` interface with `LocalDiskBackend` (Docker volume, content-addressed paths) now and `S3Backend` later. Metadata, hashes and keys live in PostgreSQL.
- **Knowledge:** Cognee with default stores persisted on a Docker volume, one dataset per project; OKF Bundle generation.
- **LLM:** Bifrost gateway → OpenRouter. SBAM uses model **aliases** (`sbam-ocr`, `sbam-sow`, `sbam-isatvon`, `sbam-codegen`, `sbam-testgen`, `sbam-corrector`) mapped to OpenRouter models in Bifrost config.
- **Orchestration:** Temporal (self-hosted, Docker, PostgreSQL persistence) for every long-running step and every human wait.
- **Code generation:** Jinja2 skeleton templates (FastMCP) + LLM body generation + `ast` validation against the manifest.
- **Quality and security gates:** Ruff, Black `--check`, MyPy `--strict`, ClamAV, Bandit *(proposed)*, import resolution in the runtime image.
- **Sandbox and tests:** CubeSandbox MicroVMs on a Linux/KVM server reached over Netbird; Promptfoo CLI in a worker container.
- **Identity:** Keycloak realm `bullm` with an `organization` (tenant) model, clients `sbam-admin` and `sbam-fde`, roles `sbam-admin`, `sbam-fde` (+ platform `super-admin`).
- **Deployment:** Docker images for everything; Docker Compose for local, dev, staging and production on VMs; GitHub Actions CI; GHCR registry *(proposed)*.
- **Observability:** OpenTelemetry (traces, metrics, logs) → collector → Grafana LGTM stack *(proposed)*; Langfuse for LLM traces; PostHog for product/eval analytics; Temporal UI for operators.

> **Important — changes from Baseline v1:** Next.js replaces Vite (C-34). DuckDB is removed (C-25). Code generation is hybrid Jinja2 + LLM (C-22). Certification is automatic and three-level (C-20, C-27). ISATVON is per Sub-Skill (C-16). The SRIM handover API, client approval and the hash-chained audit are moved to future scope. Storage is local disk behind an S3-compatible interface (C-37).

## PLAN §5. Engineering Principles


1. **SBAM is build-time only.** Nothing deploys, hot-reloads or runs a skill in production. CubeSandbox runs are isolated tests.
2. **The approved SOW is the source of the ISATVON breakdown. The Sub-Skill is the unit of implementation. The JSON manifest is the contract for code generation.**
3. **Pydantic models are the single contract.** ISATVON, manifests, SOW structure and API schemas are defined once in Python. JSON Schema and TypeScript types are generated from them.
4. **LLM output is untrusted until validated.** Every LLM result is schema-validated. SOWs pass FDE approval. Generated code passes every gate.
5. **Certification is automatic but never partial.** A Sub-Skill is certified only when every gate passes on the same code hash. Macro and Workflow certification follow only from certified children.
6. **Failures go back, never forward.** A failed gate triggers at most two automatic corrections, then returns to the FDE with the reason.
7. **Certified means frozen.** Certified artifacts are hashed and immutable. Any change creates a new version (MAJOR.0.PATCH) and is re-certified. Unchanged parts reuse their certification.
8. **Tenant and project isolation everywhere.** Every row, file path, Cognee dataset, LLM prompt and sandbox run is scoped to one tenant and one project.
9. **Temporal owns long-running work.** API handlers never block on parsing, LLM, code generation or sandbox runs.
10. **Everything important is traceable.** Every step is audited, every LLM call is traced (Langfuse) and every artifact is stored with its hash.
11. **Reuse open source, build the glue.** Cognee, OKF, Bifrost, Temporal, CubeSandbox, Promptfoo, Keycloak, Refine, AG Grid, TipTap, Uppy and Xyflow are integrated, not rebuilt.
12. **Everything runs in Docker**, from pinned and scanned images. The skill runtime image digest is recorded in every manifest, so results are reproducible.
13. **Storage and cloud are abstracted.** Local disk now, AWS S3 later, with no code change outside the storage adapter.
