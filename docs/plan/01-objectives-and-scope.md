> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# PLAN — Objectives and Scope

## PLAN §1. Executive Summary and Implementation Objectives


### 1.1 What SBAM is

SBAM is the **build-time** layer of BuLLM. An FDE (Forward Deployed Engineer) uploads a client's project information. SBAM turns it into a **certified, frozen, versioned workflow manifest**: a set of tested FastMCP tools organised as **Macro Skills (agents)** and **Sub-Skills (skills)**, each with its own ISATVON contract, JSON manifest, generated code and test evidence. SRIM (runtime) later consumes that manifest. The SRIM handover integration itself is **future scope** (QA M1–M4).

### 1.2 Where SBAM starts and ends

|                                  |                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Starts** | An **Admin** (Admin portal) creates a project for a client tenant and maintains the masters and RBAC. An **FDE** (FDE portal) opens the assigned project, uploads the project information with Uppy and drives the pipeline. **SBAM generates the Workflow** (Macro Skills → Sub-Skills) from the approved SOW; the FDE never creates a Workflow. |
| **Ends**                   | The Workflow is**certified and frozen**: every Sub-Skill has passed all gates, every Macro Skill is certified, and the Workflow manifest is certified, versioned, hashed, immutable and exportable. In MVP, handover to SRIM is a download/export of the frozen manifest package; the automated handover interface is future scope. |
| **Outputs SBAM generates** | SOW (export to PDF / DOCX / MD), ISATVON breakdown (Excel export), JSON manifests (per Sub-Skill and per Workflow), generated FastMCP code. Nothing else (QA A4, G3).                                                                                                                                                                     |

### 1.3 The confirmed flow

```text
Admin portal:  Tenant → Project → Masters context (5 masters + README) → RBAC
FDE portal:    Open project → Upload (Uppy, ClamAV, parse) + Admin context
               → Cognee (knowledge graph) → OKF Bundle → LLM (Bifrost → OpenRouter) → SOW (one shot)
               → FDE review / edit (TipTap) → FDE approves SOW (HITL gate)
               → ISATVON breakdown: Macro Skills (agents, MSnn) → Sub-Skills (skills, MSnn.m), ISATVON 1.4 per Sub-Skill
               → FDE reviews/edits in AG Grid → Generate
               → Workflow schema.sql → per Sub-Skill: Skill Manifest (ISATVON 1.4 format) → Jinja2 skeleton + LLM bodies
                 (Action tool + independent Fixed Check tool + append-only state handoff) → AST validation vs manifest
                 → Ruff / Black / MyPy strict / Bandit* / ClamAV → LLM test data + ≥3 loop scenarios (Promptfoo)
                 → CubeSandbox run through the bounded-loop harness (5 loop bounds) → auto-certify Sub-Skill (or correction ×2 → back to FDE)
               → all Sub-Skills certified → Macro Skill certified
               → all Macro Skills certified → Workflow validated → Workflow certified → frozen + versioned
               → export frozen manifest package (SRIM handover API = future)
   (* Bandit proposed; see Section 30)
```

### 1.4 SBAM → SRIM boundary

| Developer-flow step (ARCH-1)                                                                              | Owner    | In this plan                                                                 |
| --------------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------- |
| 1 Ingest · 2 SOW · 3 Approve · 4 ISATVON grid · 5 Manifest · 6 Compile · 7 Orchestrate · 8 Certify | SBAM     | Phases 3–14                                                                 |
| 9 Handover                                                                                                | Boundary | MVP: frozen package export (Phase 14). Automated handover:**[FUTURE]** |
| 10 Hot-reload · 11 Execute                                                                               | SRIM     | Out of scope                                                                 |
| 12 Reports & eval                                                                                         | BuLLMind | Out of scope                                                                 |

### 1.5 Implementation objectives

1. Multi-tenant SBAM with two Keycloak-backed portals: **Admin** (tenants' projects, five masters + README/Governance, RBAC) and **FDE** (workflows).
2. Project workspace and browser ingestion (the FDE drives the pipeline; SBAM creates the Workflow later from the approved SOW) (Uppy, ≤15 MB per file, ≤20 files per project, ClamAV scan, PDF/DOCX/XLSX/CSV/MD/TXT/EML parsing, basic OCR via LLM vision), with soft delete and 90-day recovery.
3. Per-project knowledge construction: Cognee (default stores) builds the knowledge graph from uploads plus Admin context. An information-preserving OKF Bundle is produced. FDEs can view and explore the graph but not edit it.
4. One-shot SOW generation from the OKF Bundle through Bifrost/OpenRouter, with mandatory source citations, TipTap editing, versioning, and PDF/DOCX/MD export.
5. FDE SOW approval gate (HITL). Scope changes create a new SOW version that invalidates only the affected Macro/Sub-Skills.
6. ISATVON 1.4 breakdown: SOW → Macro Skills (agents, `MSnn`) → Sub-Skills (skills, `MSnn.m`). Each Sub-Skill has a full ISATVON 1.4 definition, including the 5 mandatory loop bounds and the append-only state handoff. The FDE edits it in AG Grid and can export it to Excel.
7. A JSON manifest per Sub-Skill as the contract for code generation, plus a Workflow manifest that aggregates them.
8. Hybrid code generation: a Jinja2 FastMCP skeleton from the Skill Manifest plus LLM-written bodies. The output is an **Action tool**, an independent **Fixed Check tool**, an append-only state-handoff writer and a fresh-state reader, with no manual retry loops. AST validates the code against the manifest, and a Workflow `schema.sql` is generated and validated.
9. Static and security gates: Ruff, Black, MyPy strict, ClamAV (plus Bandit, proposed), and a dependency check against the runtime image registry.
10. LLM-generated test data and at least 3 Promptfoo test cases per Sub-Skill, executed in CubeSandbox on a Linux/KVM host with controlled network access.
11. Automatic certification: Sub-Skill → Macro Skill → Workflow. Unchanged Sub-Skills reuse their certification. MAJOR.0.PATCH versioning. Freezing with immutability. No revocation.
12. Durable orchestration of the whole pipeline in self-hosted Temporal, with in-app notifications at every step (N = Next/Notification).
13. Containerized everything: Docker Compose for local, dev, staging and production (on VMs), GitHub Actions CI with image scanning. AWS (S3 and hosting) is the future target.
14. Observability with OpenTelemetry, Langfuse and PostHog. Normal audit table. Testing, backup/restore and production readiness.

SBAM is built in a **fresh repository** as a **modular monolith** (FastAPI app + Temporal workers + Next.js app). The backend is structured so other BuLLM layers can be added later as modules. No other existing project (CAM, RFQ or the six FDE workflows) is referenced or touched (QA A2, A3).

## PLAN §2. Scope


### 2.1 In scope (MVP)

| Area          | Items                                                                                                                                                                                  |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Portals       | Admin portal (projects, masters, RBAC, tenant users) and FDE portal (workflows end to end) on Next.js + Refine                                                                         |
| Identity      | Keycloak (multi-tenant, two client apps/portals, roles), JWT validation in FastAPI                                                                                                     |
| Ingestion     | Uppy browser upload, ClamAV, type detection, parsing, basic OCR through LLM vision, soft delete + 90-day recovery                                                                      |
| Knowledge     | Cognee per-project dataset (default stores), Admin context injection, OKF Bundle, read-only graph explorer                                                                             |
| SOW           | One-shot generation, citations, TipTap editing, versioning, FDE approval, PDF/DOCX/MD export, scope-change versioning with impact analysis                                             |
| ISATVON       | Macro → Sub-Skill breakdown, ISATVON per Sub-Skill, AG Grid workbench (FDE only), Excel export                                                                                        |
| Build         | Sub-Skill manifests, hybrid Jinja2 + LLM code generation, AST + static + security gates, correction loop (2 attempts)                                                                  |
| Test          | LLM-generated mock data, ≥3 Promptfoo tests per Sub-Skill, CubeSandbox execution with controlled egress                                                                               |
| Certification | Automatic Sub-Skill, Macro and Workflow certification, reuse, versioning, freeze, export package                                                                                       |
| Platform      | Temporal, PostgreSQL, local-disk storage adapter, Bifrost → OpenRouter, OTEL + Langfuse + PostHog, in-app notifications, audit table, Docker Compose (4 environments), GitHub Actions |

### 2.2 Out of scope

| Item                                                                              | Owner / reason                                           |
| --------------------------------------------------------------------------------- | -------------------------------------------------------- |
| SRIM runtime, hot-reload, skill palette, execution, mock/live switch toggle       | SRIM                                                     |
| Automated SBAM → SRIM handover API, acknowledgement, rejection handling          | **[FUTURE]** (QA M1–M4)                           |
| Reports beyond SOW, ISATVON Excel, manifests and code                             | BuLLMind (QA A4)                                         |
| The six FDE manufacturing workflows (RFQ, Quote, Reflow, Vision, Quality, Assets) | Not referenced (QA A3)                                   |
| Client logins or external approvers                                               | Not in MVP (QA B3)                                       |
| Manual creation of ISATVON rows from scratch                                      | Not supported (QA G5)                                    |
| Revocation of certified/frozen workflows                                          | Not supported (QA L1). A new version is created instead. |
| DuckDB                                                                            | Removed. PostgreSQL only (QA J1).                        |

### 2.3 Future scope

| Item                                                                  | Source    |
| --------------------------------------------------------------------- | --------- |
| Email or other integrations for SOW approval                          | QA B3     |
| API / webhook / cron / email / FastMCP-watchdog ingestion             | QA D2     |
| Advanced OCR                                                          | QA D5     |
| AWS hosting and AWS S3 storage                                        | QA N2, N5 |
| SRIM handover API, acknowledgement, environment switch                | QA M1–M4 |
| Manifest signing (cosign/KMS)                                         | QA H3     |
| Compliance (ISO 27001 / SOC 2 / GDPR / DPDP) and tamper-evident audit | QA P1, P2 |
| Scaling beyond 10 concurrent users                                    | QA R1     |
