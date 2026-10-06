# SBAM — Implementation Plan

**Project:** BuLLM Skills Build Acceleration Manager (SBAM) — the build, validation and certification layer of the BuLLM platform
**Stack:** Next.js (App Router) + Refine + AG Grid + TipTap +  Uppy + Xyflow · FastAPI + Pydantic / Pydantic AI · PostgreSQL · local-disk object storage behind an S3-compatible interface (AWS S3 later) · Cognee (default stores) + OKF · Bifrost → OpenRouter · Temporal (self-hosted) · Jinja2 + LLM hybrid code generation + `ast` · Ruff / Black / MyPy strict / ClamAV · CubeSandbox · Promptfoo · Keycloak · Docker Compose · GitHub Actions · OpenTelemetry + Langfuse + PostHog


**Document Status:** Implementation Baseline v4. It includes the stakeholder answers in `SBAM_Plan_Open_Questions.md` (5 Oct 2026), the confirmed choices on code generation, certification gates, storage and hierarchy, the **consolidated admin masters workbook** (MSC, BAM 0.2 structure frozen 5 Oct 2026) and **ISATVON 1.4** (ISA14). Items still open are listed in Section 30.


**Purpose:** Engineering delivery plan for SBAM, aligned with *BuLLM / SBAM: build, validation & certification — Four-step process* (PD, 28 Sep 2026), the architecture diagrams ARCH-1 (*BuLLM Platform — One Architecture*), ARCH-2 (*FDE Acceleration Platform Architecture*) and ARCH-3 (*SBAM System Architecture*), the answered question set (QA), *BuLLM – Admin Masters (One Sheet) – Consolidated.xlsx* (MSC, which supersedes the earlier Manufacturing Template), *ISATVON 1.4 for Skill Generation* (ISA14, v1.4.0), and the companion design document **`SBAM_Technical_Design.md` (TD)**.

### How the two documents fit together

| Document                                          | Contains                                                                                                                                                                                                                                                                    | Use it for                            |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| **SBAM_Implementation_Plan.md** (this file) | Objectives, scope, confirmed decisions, architecture baseline, principles, phases 0–16 with checklists and exit criteria, developer task breakdown, 4-developer allocation and timeline, MVP, DoD, release gate, risks, open items, traceability, roadmap                  | Planning, tracking and assigning work |
| **SBAM_Technical_Design.md**                | User flow, system flow, components, repository structure, database, APIs, screens, ISATVON and manifest contracts, code generation, Temporal design, state machines, sandbox/testing design, security, observability, error matrix, test scenarios, Docker and environments | Building each module correctly        |

Markers used: **[REQUIRED]** must be built · **[INTEGRATION]** adopt an OSS component · **[FUTURE]** later release · **[PENDING INPUT]** waiting for an attachment or confirmation (Section 30) · *(proposed)* a recommendation of this plan, open to change.

---

# 1. Executive Summary and Implementation Objectives

## 1.1 What SBAM is

SBAM is the **build-time** layer of BuLLM. An FDE (Forward Deployed Engineer) uploads a client's project information. SBAM turns it into a **certified, frozen, versioned workflow manifest**: a set of tested FastMCP tools organised as **Macro Skills (agents)** and **Sub-Skills (skills)**, each with its own ISATVON contract, JSON manifest, generated code and test evidence. SRIM (runtime) later consumes that manifest. The SRIM handover integration itself is **future scope** (QA M1–M4).

## 1.2 Where SBAM starts and ends

|                                  |                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Starts** | An **Admin** (Admin portal) creates a project for a client tenant and maintains the masters and RBAC. An **FDE** (FDE portal) opens the assigned project, uploads the project information with Uppy and drives the pipeline. **SBAM generates the Workflow** (Macro Skills → Sub-Skills) from the approved SOW; the FDE never creates a Workflow. |
| **Ends**                   | The Workflow is**certified and frozen**: every Sub-Skill has passed all gates, every Macro Skill is certified, and the Workflow manifest is certified, versioned, hashed, immutable and exportable. In MVP, handover to SRIM is a download/export of the frozen manifest package; the automated handover interface is future scope. |
| **Outputs SBAM generates** | SOW (export to PDF / DOCX / MD), ISATVON breakdown (Excel export), JSON manifests (per Sub-Skill and per Workflow), generated FastMCP code. Nothing else (QA A4, G3).                                                                                                                                                                     |

## 1.3 The confirmed flow

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

## 1.4 SBAM → SRIM boundary

| Developer-flow step (ARCH-1)                                                                              | Owner    | In this plan                                                                 |
| --------------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------- |
| 1 Ingest · 2 SOW · 3 Approve · 4 ISATVON grid · 5 Manifest · 6 Compile · 7 Orchestrate · 8 Certify | SBAM     | Phases 3–14                                                                 |
| 9 Handover                                                                                                | Boundary | MVP: frozen package export (Phase 14). Automated handover:**[FUTURE]** |
| 10 Hot-reload · 11 Execute                                                                               | SRIM     | Out of scope                                                                 |
| 12 Reports & eval                                                                                         | BuLLMind | Out of scope                                                                 |

## 1.5 Implementation objectives

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

---

# 2. Scope

## 2.1 In scope (MVP)

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

## 2.2 Out of scope

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

## 2.3 Future scope

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

---

# 3. Confirmed Decisions

| ID   | Topic                            | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Source                     |
| ---- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| C-01 | Repository                       | Fresh repository; nothing exists; no other project referenced                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | QA A2, A3                  |
| C-02 | Portals and roles                | Two logins:**Admin** (masters, RBAC, projects) and **FDE** (workflows). The FDE approves SOWs. Certification is automatic. No Reviewer/Approver/Certifier roles.                                                                                                                                                                                                                                                                                                                                                                                                                                                      | QA A1, B1–B5              |
| C-03 | Identity                         | Keycloak                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | QA B6                      |
| C-04 | Tenancy                          | Multi-tenant; client organisations isolated                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | QA B7                      |
| C-05 | Hierarchy | Tenant → Project (Admin creates; holds masters context, documents, knowledge and one SOW; FDEs assigned) → FDE drives the project pipeline → **SBAM generates the Workflow** from the approved SOW → Macro Skills → Sub-Skills. **The FDE never creates a Workflow.** | QA C1, C3, flow, follow-up, owner correction 6 Oct 2026 |
| C-06 | Masters                          | README + five masters (**Organization, Rules, Agents, Skills, Filetype**), tenant-scoped, used as reference context for Cognee/OKF with Organization as graph root. **Frozen BAM 0.2 structure** from MSC: exact tab names, column names/order, 3 header rows, row labels, 43 frozen filetypes. Stable IDs and status are read from inside the rows (no extra columns). RAG Reference JSON drives graph node type and parent. Admin imports/exports the workbook in this layout (TD §7.3.1).                                                                                                                         | QA C2–C4, MSC             |
| C-07 | Upload limits                    | ≤15 MB per file, ≤20 files per project (configurable)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | QA D3                      |
| C-08 | File types                       | PDF, DOCX, XLSX/CSV, MD, TXT, EML, plus other easily extractable text formats                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | QA D1                      |
| C-09 | Malware / security scan          | ClamAV for uploads and generated artifacts                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | QA D4, J2                  |
| C-10 | OCR                              | MVP: basic extraction plus LLM vision via Bifrost/OpenRouter for scanned pages; better OCR later                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | QA D5                      |
| C-11 | Deletion                         | Soft delete with 90-day recovery, then purge                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | QA D6                      |
| C-12 | Cognee                           | Default Cognee stores; isolated per project; graph viewable/explorable, not editable                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | QA E1, E3, E4              |
| C-13 | OKF                              | OKF Bundle must preserve all ingested information (content + entities + relations + provenance) so the LLM has full context for the SOW                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | QA E2                      |
| C-14 | SOW                              | One-shot generation → HITL review/edit in TipTap → FDE approves. Citations mandatory. Export PDF/DOCX/MD. SOW structure is derived from ISATVON 1.4 (TD §10.6).                                                                                                                                                                                                                                                                                                                                                                                                                                                                | QA F1–F4                  |
| C-15 | Scope change                     | New SOW version; only affected Macro/Sub-Skills are invalidated and rebuilt                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | QA F5                      |
| C-16 | ISATVON level                    | Macro Skill = agent (major module,`MSnn_<Name>`); Sub-Skill = skill (smallest implementable feature, `MSnn.m_<Name>`). **ISATVON 1.4 definition per Sub-Skill.**                                                                                                                                                                                                                                                                                                                                                                                                                                                        | QA G2, ISA14               |
| C-17 | Excel                            | Export only (no re-upload)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | QA G3                      |
| C-18 | N field                          | "Next / Notification": notify in-app, then move to the next step                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | QA G4, Q2                  |
| C-19 | Grid editing                     | FDE only; no manual from-scratch rows; no real-time multi-user editing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | QA G5, G6                  |
| C-20 | Certification levels             | Sub-Skill (unit), Macro Skill (all subs certified) and Workflow/project (all macros certified + workflow validation)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | QA H1, flow                |
| C-21 | Manifest for SRIM                | The generated JSON manifest is what SRIM will use                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | QA H2                      |
| C-22 | Code generation                  | **Hybrid**: Jinja2 skeleton (FastMCP server, imports, Pydantic I/O models from manifest) + LLM-written function bodies; AST validates against the manifest                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | QA I1, follow-up           |
| C-23 | Artifact type                    | FastMCP tools only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | QA I3                      |
| C-24 | Correction                       | LLM may fix the ISATVON JSON**and** edit Python; 2 automatic attempts, then back to the FDE                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | QA I4, I5                  |
| C-25 | Dependencies                     | Everything runs in Docker; SBAM provides a curated runtime with all allowed libraries; PostgreSQL only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | QA J1                      |
| C-26 | MyPy                             | Strict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | QA J3                      |
| C-27 | Certification gates              | AST vs manifest + Ruff/Black/MyPy strict + security scan + ≥3 Promptfoo tests on LLM-generated data in CubeSandbox; all must pass                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | QA K1–K5, follow-up       |
| C-28 | Sandbox                          | CubeSandbox on a Linux server (KVM); network access allowed (controlled)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | QA K1, K4                  |
| C-29 | Test data                        | LLM-generated                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | QA K2                      |
| C-30 | Promptfoo authoring              | Hybrid*(plan recommendation adopted)*: auto-generated deterministic assertions from the O-field + LLM-generated scenarios; FDE can review/add                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | QA K3                      |
| C-31 | Revocation                       | Not allowed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | QA L1                      |
| C-32 | Versioning                       | MAJOR.0.PATCH: 1.0.0 → 2.0.0 for major, 1.0.1, 1.0.2 … for minor                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | QA L2                      |
| C-33 | Reuse                            | Unchanged Sub-/Macro Skills reuse previous certification                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | QA L3                      |
| C-34 | Frontend                         | Next.js (not Vite)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | QA N1                      |
| C-35 | Hosting                          | Local now (Docker Compose on VMs); AWS later                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | QA N2, N3                  |
| C-36 | Temporal                         | Self-hosted in Docker                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | QA N4                      |
| C-37 | Storage                          | Local disk volume behind an S3-compatible storage interface; metadata in PostgreSQL; AWS S3 later                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | QA N5, follow-up           |
| C-38 | CI                               | GitHub Actions (GHCR proposed as registry)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | QA N6                      |
| C-39 | Environments                     | local, dev, staging, production                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | QA N8                      |
| C-40 | Dev machines                     | 32–64 GB RAM; the full stack incl. Langfuse runs locally (PostHog optional profile)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | QA N9                      |
| C-41 | LLM                              | OpenRouter through Bifrost; model chosen per task; external providers allowed; no budget limit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | QA O1–O3                  |
| C-42 | Compliance / audit               | None for now; normal audit table                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | QA P1, P2                  |
| C-43 | Observability                    | OTEL + Langfuse + PostHog all in MVP; alerts only inside SBAM                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | QA Q1, Q2                  |
| C-44 | NFRs                             | ≤10 concurrent users for MVP; proposed latency/availability/recovery targets accepted                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | QA R1, R2                  |
| C-45 | Team                             | 4 developers, all skills; deliver as fast as possible without missing scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | QA S1, S2                  |
| C-46 | Documents                        | Split into plan + technical design; Markdown; detailed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | QA T1–T3                  |
| C-47 | Netbird*(plan recommendation)* | Private WireGuard mesh linking dev laptops, dev/staging/prod VMs and the CubeSandbox Linux host; nothing exposed publicly except the portal reverse proxy                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | QA N7                      |
| C-48 | ISATVON contract                 | **ISATVON 1.4** (Declarative Bounded Iteration). I: trigger/action/check/bound + user role, task, context. S: required inputs + fresh state parameters (+ external/ML data). A: orchestration, action step, acceptance check, SRIM metadata. T: execution layer, action tool, **independent** fixed check tool (+ secondary tools, gateway). V: **5 mandatory loop bounds** (max_iterations, success_gate, no_op_condition, escalation_condition, budget_limit) + tables, secrets. O: output JSON schema + **append-only state handoff** + success criteria. N: UI updates, DB updates, alerts. (TD §10) | ISA14                      |
| C-49 | Sub-Skill manifest               | Exactly the ISA14 Skill Manifest structure (`skill_metadata`, `isatvon_definition`, `srim_runtime_definition`, `contracts`, `governance`) plus one SBAM extension block `sbam_build_record` (TD §11.1)                                                                                                                                                                                                                                                                                                                                                                                                               | ISA14, QA H2               |
| C-50 | Generated code                   | Per Sub-Skill: Action tool + independent Fixed Check tool (no cross-imports), state-handoff writer (insert-only), fresh-state reader;**no manual retry loops** because iteration is declarative                                                                                                                                                                                                                                                                                                                                                                                                                             | ISA14                      |
| C-51 | Loop testing                     | SBAM bounded-loop harness emulates the declarative loop in CubeSandbox; ≥3 scenarios: PASS_FIRST, ESCALATION and one of PASS_AFTER_RETRY / NO_OP / EDGE                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | ISA14, QA K5               |
| C-52 | Manifest status mapping          | SBAM certified/frozen = manifest`status: APPROVED`, `governance.lifecycle.state: APPROVED`, `immutable_after_approval: true`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | ISA14                      |
| C-53 | Ingestion types                  | The 43 frozen Filetype entries drive accepted types and loaders (loader flag in platform config, not in the tab). MVP = tier-1 text/structured types, tier-2 office/PDF/CSV/archives, basic LLM OCR for scanned PDF and images; CAD/PCB/media/Google/Web = future (TD §5.2). The Filetype table is private: FDEs cannot edit it.                                                                                                                                                                                                                                                                                                 | MSC, QA D1                 |
| C-55 | Master documents                 | Admins can attach context files to masters; each is routed to a master by the MSC Filetype default route (intent class), and the Admin confirms or overrides the route (TD §7.3.2)                                                                                                                                                                                                                                                                                                                                                                                                                                               | MSC README §7, §9        |
| C-54 | Foundational schema              | A Workflow`schema.sql` (uploaded `.sql` or generated from V.tables) is validated and used in every sandbox run, and shipped in the export ("Contract-Driven Development")                                                                                                                                                                                                                                                                                                                                                                                                                                                     | ISA14                      |

> **Important — two terms called "workflow":** In this plan, **Workflow** (capital W) means the SBAM business object that **SBAM generates from the approved SOW** (its Macro and Sub-Skills, certified and frozen as one unit). The FDE never creates it; the FDE drives the **project pipeline**. Temporal's durable executions are always called **Temporal workflows**.

---

# 4. Architecture Baseline

## 4.1 Application architecture

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

## 4.2 Stack decisions

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

---

# 5. Engineering Principles

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

---

# 6. Phase 0 — Architecture, Contracts and Inputs Baseline

**Goal:** Freeze the contracts and collect the missing inputs (masters schema, ISATVON sheet, SOW template) so the four developers can build in parallel without rework.

**Owner:** Tech lead + all · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** — · **Blocks:** everything
**Design references:** TD §2 (terminology), TD §10 (ISATVON), TD §11 (manifests), TD §14 (state machines).

### Inputs (received)

- [ ] Convert the consolidated masters workbook (MSC, frozen BAM 0.2 structure) into six JSON Schemas (README, Organization, Rules, Agents, Skills, Filetype), the RAG Reference JSON schema, stable-ID/status extraction rules and fixtures from its rows (TD §7.3.1).
- [ ] Convert ISATVON 1.4 (ISA14) into the strict Pydantic contract (TD §10.3) and the Skill Manifest model (TD §11.1); use the ISA14 sample manifest (MS01.1) as the golden fixture.
- [ ] Freeze the SOW structure derived from ISATVON 1.4 (TD §10.6).

### Contracts to freeze

- [ ] ISATVON 1.4 Sub-Skill contract (TD §10.3–10.4): fields, the 5 loop bounds, the safe expression grammar for gates, required/optional, validation rules.
- [ ] Macro Skill (agent) structure and Sub-Skill dependency rules (TD §10.2).
- [ ] Skill Manifest (ISA14 structure + `sbam_build_record`), Workflow manifest and the hash rule (TD §11).
- [ ] Loop-harness semantics (`loop_spec.json`) shared with the SRIM owners for parity (O-17).
- [ ] State machines for Workflow, Document, SOW, Sub-Skill, Macro Skill and Workflow certification (TD §14).
- [ ] Versioning rules for MAJOR.0.PATCH: what is major and what is minor (TD §11.4).
- [ ] API conventions (TD §8.1) and the generated TS type pipeline.
- [ ] Keycloak tenancy model: one realm with Organizations, or a realm per tenant (Section 30, O-05).
- [ ] Jinja2 skeleton contract: action/check tool modules, protected regions, LLM body slots, handoff writer, allowed imports (TD §12).

### Spikes (time-boxed, parallel)

- [ ] Cognee with default stores: per-project dataset isolation, Admin-context injection, LLM/embedding calls routed via Bifrost/OpenRouter.
- [ ] OKF Bundle: export from Cognee that preserves full content, entities, relations and provenance; measure size against model context.
- [ ] CubeSandbox on the Linux/KVM server: template from a Docker image, run a FastMCP tool, controlled egress, start latency.
- [ ] Hybrid codegen: Jinja2 skeleton + LLM bodies (Pydantic AI) → action + check tools + handoff + AST checks on the ISA14 sample skill and one MSC-domain skill (e.g., SK-BOM scrub).
- [ ] Promptfoo against a FastMCP tool executed in CubeSandbox.
- [ ] Keycloak + Next.js (Auth.js) + FastAPI JWT validation with tenant claim.

### Exit Criteria

- [ ] ISATVON v1, manifest v1 and state-machine documents are approved and committed as JSON Schemas/ADRs.
- [ ] Master schemas + demo rows, the ISA14 sample manifest and the SOW structure are in the repository as fixtures.
- [ ] All six spikes have written results, and no blocking unknown remains for Phases 1–5.
- [ ] Every developer knows their stream for the first three weeks (Section 24).

---

# 7. Phase 1 — Repository Foundation and Containerized Dev Stack

**Goal:** Create the fresh monorepo, the full Docker Compose stack and CI, so any developer can run SBAM locally on day one.

**Owner:** Dev A (platform) · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 0
**Design references:** TD §6 (repo structure), TD §20 (Docker & environments).

### Repository

- [ ] Repository layout in `solutionsDigibull/BAM` (`client/`, `server/{api,workers,contracts,templates,tests}`, `compose/`, `docs/`) per TD §6.
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

- [ ] Dockerfiles: `sbam-web`, `sbam-api`, `sbam-worker`, `sbam-toolchain`, `sbam-promptfoo`, `skill-runtime` (TD §20.2).
- [ ] `compose.yaml` with profiles `core`, `obs`, `analytics`, `sandbox-local` and `compose.override.yaml` for hot reload (TD §20.3).
- [ ] Init jobs: Postgres databases (sbam, temporal, temporal_visibility, keycloak, langfuse), Keycloak realm import, Temporal namespace, storage volume layout, ClamAV signature update.
- [ ] `.env.example`, Makefile (`up`, `down`, `logs`, `seed`, `test`, `e2e`, `reset`), README "running in 15 minutes".
- [ ] GitHub Actions: lint → types → unit → API tests → hadolint → build images → Trivy → SBOM → Compose smoke test → push to GHCR on `main`.

### Exit Criteria

- [ ] `make up` brings up the `core` profile healthy on Windows (Docker Desktop, WSL2), macOS and Linux with 32 GB RAM.
- [ ] A sample Temporal workflow runs on every worker queue and is visible in Temporal UI.
- [ ] CI runs on every PR and blocks on lint, type, test, image-scan (CRITICAL) and contract-drift failures.
- [ ] Generated TS types come only from Pydantic models.

---

# 8. Phase 2 — Identity, Multi-Tenancy and RBAC

**Goal:** Build the security boundary (Keycloak, tenant isolation and the two portals) before any client data enters the system.

**Owner:** Dev A · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 1
**Design references:** TD §7 (tenancy tables), TD §8.2, TD §16.

### Keycloak

- [ ] Realm `bullm`; clients `sbam-admin` and `sbam-fde` (OIDC, PKCE); roles `super-admin`, `sbam-admin`, `sbam-fde`; tenant membership (Organizations or group-based per O-05).
- [ ] Token mappers: `tenant_id`, roles, `sub`; short access-token lifetime; refresh rotation.
- [ ] Realm export committed for repeatable local, dev and staging setup.

### Backend authorization

- [ ] JWT validation (JWKS cache), current-user dependency, tenant resolution.
- [ ] Authorization chain:

```text
Valid Keycloak token
        ↓
Tenant (tenant_id claim must match resource)
        ↓
Portal role (sbam-admin for Admin APIs, sbam-fde for FDE APIs)
        ↓
Project access (FDE assigned to project)
        ↓
Resource state guard (e.g., approved SOW is read-only, frozen Workflow is immutable)
```

- [ ] Tenant-scoped repository base class (every query filtered by `tenant_id`); cross-tenant access returns 404.
- [ ] RBAC management APIs for Admin: invite or assign users, assign FDEs to projects.
- [ ] Audit table writes for all security-relevant actions.

### Frontend

- [ ] Separate Admin and FDE login entry points and landing pages; role-based route protection (UX only, backend authoritative).
- [ ] Admin "Users & Roles" screen.

### Tests

- [ ] RBAC matrix test (each role × each endpoint).
- [ ] Cross-tenant and cross-project IDOR tests.
- [ ] Token expiry, refresh and logout tests.

### Exit Criteria

- [ ] An FDE of tenant A cannot see, list or infer any resource of tenant B, or of a tenant-A project they are not assigned to.
- [ ] Admin APIs reject FDE tokens, and FDE APIs reject Admin-only actions.
- [ ] Every login, role change and project assignment is recorded in the audit table.

---

# 9. Phase 3 — Admin Portal: Projects and Masters

**Goal:** Let Admins create projects and maintain the masters that give Cognee the reference context for each project.

**Owner:** Dev D (UI) + Dev A (API) · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 2
**Design references:** TD §3 (Admin flow), TD §7 (masters tables), TD §9.2 (Admin screens).

### Backend

- [ ] Projects: create, edit, archive, assign FDEs, project-level context notes.
- [ ] Generic masters engine: master type → JSON Schema → versioned entries; tenant-scoped with optional project binding.
- [ ] Six master schemas exactly as in MSC (frozen BAM 0.2 columns): Organization (14), Rules (11), Agents (11), Skills (11), Filetype (5), README.
- [ ] Stable-ID and status extraction from row content (Code, `RULE-`, `AG-`, `SK-` prefixes; `Status:` inside Misc), stored as system metadata. Status edits are written back into Misc.
- [ ] RAG Reference JSON validation (7 keys) and mapping to Cognee node type, node id, parent edge and access level.
- [ ] Workbook import (structure check against the frozen layout; validate-only and upsert; row-level report) and export in exactly the same layout; empty template download.
- [ ] Seed demo data by importing MSC.
- [ ] Filetype: 43 frozen entries, read-only for FDE; `mvp_enabled` loader flag and default route held in platform configuration.
- [ ] Master documents: upload → ClamAV → route by Filetype default route (intent class) → Admin confirms or overrides → parsed and added as master context.
- [ ] Masters snapshot API: the exact master versions used for a project's knowledge build (for traceability).
- [ ] Hook: master changes mark dependent project knowledge as "context outdated" (no automatic rebuild).

### Frontend (Admin portal)

- [ ] Project list/create/edit, FDE assignment.
- [ ] Masters screen: one tab per sheet, frozen BAM 0.2 columns with row-3 descriptions as tooltips, stable-ID and status chips, RAG Reference editor, guideline help per master, master-documents panel, version history, Import/Export workbook; Filetype tab read-only.

### Tests

- [ ] Import of MSC round-trips (import → export → identical cells); a workbook with a renamed/added/reordered column is rejected with a diff; invalid rows are reported with tab/row/column.
- [ ] Project-specific entries override tenant-wide ones in the snapshot.

### Exit Criteria

- [ ] An Admin can create a project, assign FDEs and maintain all masters with demo data loaded.
- [ ] A project's master-context snapshot can be produced, versioned and handed to the knowledge pipeline.

---

# 10. Phase 4 — Project Workspace and Document Ingestion

**Goal:** Let FDEs open their assigned project and upload project information safely, with every file scanned, parsed, versioned and recoverable, and start the project pipeline (SBAM creates the Workflow later, from the approved SOW).

**Owner:** Dev B (pipeline) + Dev D (UI) · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 3
**Design references:** TD §3, TD §4 (T1–T6), TD §7, TD §8.4–8.5, TD §9.3.

### Project workspace

- [ ] FDE project workspace: assigned projects list, project overview with pipeline timeline; no Workflow creation by the FDE.
- [ ] `ProjectPipeline` Temporal workflow started when the FDE clicks **Start processing**; project pipeline state timeline (TD §14.1) and dashboard endpoint.

### Upload

- [ ] Uppy (Dashboard + tus) in the FDE portal; tusd service writing to the storage volume.
- [ ] Limits enforced server-side: ≤15 MB per file, ≤20 active files per project (configurable).
- [ ] Type allowlist from the Filetype master (MVP set in TD §5.2: tier-1 text/structured, tier-2 office/PDF/CSV/archives, scanned PDF/images via OCR); magic-byte validation; SHA-256 duplicate detection.
- [ ] ClamAV (clamd) scan before acceptance; infected files quarantined and reported.

### Parsing

- [ ] `DocumentIngestion` Temporal workflow: scan → detect → parse → normalize (Markdown + JSON sidecar with page/sheet/cell provenance).
- [ ] Loaders per Filetype entry: PDF text, DOCX, XLSX/CSV (openpyxl/csv), MD/TXT, EML/MSG (attachments as child documents), JSON/XML/YAML/OpenAPI/Swagger/FastMCP spec (structural pre-parse), source code (.py via `ast`, others as text), ZIP/TAR.GZ (members ingested individually).
- [ ] Basic OCR: pages with no text layer and images are sent to a vision model via Bifrost alias `sbam-ocr`. Result marked `ocr=llm`.
- [ ] Parse report per document (pages, sheets, warnings, OCR pages).

### Deletion and recovery

- [ ] Soft delete with `deleted_at`; recovery within 90 days; Temporal schedule purges after 90 days (file + derivatives + Cognee data reference).
- [ ] Deleting a document after SOW approval flags the Workflow "context changed" (it never silently alters approved artifacts).

### Frontend (FDE portal)

- [ ] Workflow list/create; Upload screen; Processing status screen (scan, parse, OCR, errors, retry); Recycle bin (restore within 90 days).

### Tests

- [ ] EICAR test file is rejected; oversize and over-count are rejected; spoofed extension is rejected.
- [ ] Each supported type parses its fixture; scanned PDF goes through LLM OCR.
- [ ] Delete → restore → purge lifecycle with a time-skipping Temporal test.

### Exit Criteria

- [ ] An FDE can upload up to 20 files of supported types and see each reach `PARSED` or a clear failure reason.
- [ ] No unscanned or infected file is ever parsed or sent to an LLM.
- [ ] Deleted documents are restorable for 90 days and purged afterwards.

---

# 11. Phase 5 — Knowledge Construction (Cognee, OKF Bundle, Graph Explorer)

**Goal:** Build a per-project knowledge graph from uploads plus Admin context, and an information-preserving OKF Bundle that gives the LLM full context for the SOW.

**Owner:** Dev B · **Indicative effort:** 2 weeks · **Complexity:** L · **Depends on:** Phase 4
**Design references:** TD §4 (T7–T9), TD §5.3, TD §13.

### Backend

- [ ] Cognee adapter with default stores (Docker volume), one dataset per project, access-control mode verified for isolation.
- [ ] Route Cognee LLM and embedding calls through Bifrost (OpenRouter).
- [ ] Knowledge Temporal workflow, started automatically when ingestion completes: add uploads + master-context snapshot → cognify → export OKF Bundle → validate → mark READY.
- [ ] OKF Bundle content (TD §5.3): full normalized text chunks, entities, relations, master references, provenance (document version, page/sheet/cell) and bundle manifest + hash.
- [ ] Knowledge versioning: every rebuild creates a new knowledge version; the SOW records which version it used.
- [ ] Graph read API for the explorer (nodes/edges paging, search, node detail with sources).

### Frontend (FDE portal)

- [ ] Knowledge Explorer: read-only Xyflow graph (search, filter by type, expand neighbours, open source passage). No editing.
- [ ] Knowledge status and coverage panel (documents included, entities, relations, failed items).

### Tests

- [ ] Project A's dataset is never readable from project B.
- [ ] OKF Bundle round-trip: every uploaded document's content is represented, and provenance resolves to the source.
- [ ] A failure on one document does not block the bundle for the others (failure listed).

### Exit Criteria

- [ ] After ingestion, a READY knowledge version and a valid OKF Bundle exist without manual steps.
- [ ] The FDE can explore the graph and open the source passage of any node.
- [ ] The OKF Bundle preserves 100% of parsed documents (coverage report = all documents or explicitly listed failures).

---

# 12. Phase 6 — SOW Generation, Editing and Export

**Goal:** Generate a complete, cited SOW in one shot from the OKF Bundle, and let the FDE review, edit, version and export it.

**Owner:** Dev B (generation) + Dev D (TipTap) · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 5
**Design references:** TD §4 (T10–T12), TD §8.7, TD §9.3.

### Backend

- [ ] `SowDocument` Pydantic model with the ISATVON 1.4-derived sections (TD §10.6). Each feature item captures trigger, action, check, bound, inputs/state, tools, tables, success criteria, output, handoff and notifications. Every section carries citations.
- [ ] SOW Temporal workflow, started automatically after the knowledge version is READY: build context from the OKF Bundle (full bundle if it fits the model context, otherwise ranked selection per section) → one-shot generation via `sbam-sow` → validate → persist v1.
- [ ] Bounded re-ask (≤3) on schema failure; then `GENERATION_FAILED` with reason and a regenerate action.
- [ ] Versioning: every save is an immutable version with hash; diff between versions.
- [ ] Export: PDF, DOCX and Markdown renderers *(proposed: Markdown → Pandoc/WeasyPrint in worker container)*.
- [ ] Langfuse tracing and `llm_calls` provenance (model, prompt version, tokens).

### Frontend (FDE portal)

- [ ] SOW Review screen: TipTap editor, citation side panel (click opens source passage), version history and diff, regenerate, export menu.

### Tests

- [ ] Golden fixtures: SOW is schema-valid, every section cited, and all mandatory sections present.
- [ ] Prompt-injection document cannot alter the SOW structure or trigger actions.
- [ ] Exports open correctly in Word, PDF readers and Markdown.

### Exit Criteria

- [ ] For each fixture project, a complete cited SOW is generated automatically after ingestion and is editable in TipTap.
- [ ] Every SOW version is immutable, hashed and exportable to PDF/DOCX/MD.

---

# 13. Phase 7 — SOW Approval (HITL) and Scope-Change Versioning

**Goal:** Make FDE approval the hard gate before the ISATVON breakdown, and handle scope changes by versioning the SOW and invalidating only what changed.

**Owner:** Dev B + Dev D · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 6
**Design references:** TD §14.3, TD §4 (T12–T13).

### Backend

- [ ] Approve action (FDE): binds the exact SOW version hash, approval timestamp and FDE identity; locks that version.
- [ ] The Temporal pipeline waits for the `sow_approved` signal before the ISATVON breakdown.
- [ ] Scope change: the FDE creates a new SOW version from the approved one → edit → approve again.
- [ ] Impact analysis: compare old and new SOW sections; map changed sections to Macro/Sub-Skills via `sow_refs`; mark only affected Sub-Skills `INVALIDATED`; unchanged Sub-Skills keep their certification (C-33).
- [ ] MAJOR version increment of the Workflow on approval of a new SOW version (TD §11.4).

### Frontend

- [ ] Approve button with confirmation; approved banner; "Create new SOW version" action; impact preview (which Macro/Sub-Skills will rebuild).

### Tests

- [ ] An unapproved SOW cannot start the ISATVON breakdown (API returns 409).
- [ ] Scope change on one section invalidates only the Sub-Skills that cite it.

### Exit Criteria

- [ ] No ISATVON breakdown can run from an unapproved SOW version.
- [ ] A new SOW version rebuilds only the affected Sub-Skills, and the impact preview matches the actual rebuild.

---

# 14. Phase 8 — ISATVON Breakdown and Workbench

**Goal:** Break the approved SOW into Macro Skills (agents) and Sub-Skills (skills), with a complete ISATVON JSON per Sub-Skill, and give the FDE a safe AG Grid workbench to review and edit it.

**Owner:** Dev B (extraction) + Dev D (grid) · **Indicative effort:** 2 weeks · **Complexity:** XL · **Depends on:** Phase 7
**Design references:** TD §10, TD §8.8, TD §9.3.

### Backend

- [ ] On SOW approval, SBAM creates the **Workflow** record for that approved SOW version (one Workflow per approved SOW version), then runs the breakdown into it.
- [ ] Two-pass extraction via `sbam-isatvon` (Pydantic AI): pass 1 = Macro Skills (`MSnn_<Name>`, optional Agents-master ref) from the approved SOW + OKF context; pass 2 = Sub-Skills (`MSnn.m_<Name>`) per Macro with the full ISATVON 1.4 definition.
- [ ] Validation per Sub-Skill (TD §10.4): all elements and sub-fields, ID patterns, I/A consistency, action tool ≠ fixed check tool, the 5 loop bounds and gate expressions, contracts, state-handoff table in V.tables, N progress/escalation messages, libraries in registry, SOW citations.
- [ ] Regenerate actions: re-extract one Macro Skill, or re-extract one Sub-Skill, with an optional FDE hint.
- [ ] Edit APIs (FDE only): edit Sub-Skill fields, delete a Sub-Skill, reorder. No manual creation from scratch (C-19).
- [ ] Excel export via openpyxl (one sheet per Macro Skill, one row per Sub-Skill, ISATVON columns).
- [ ] N field handling: every state change emits an in-app notification and advances to the next step (C-18).

### Frontend (FDE portal)

- [ ] ISATVON Workbench: AG Grid tree data (Macro → Sub-Skills); ISATVON 1.4 column groups from the published metadata schema (never from row keys); nested-field path accessors; JSON editors for schemas; expression editor with live parse for success/no-op/escalation gates; read-only status column.
- [ ] Safeguards: loading overlay, empty state (`rows ?? []`), validation-error highlighting, revalidation on every edit, Generate disabled until all Sub-Skills are valid.
- [ ] Sub-Skill detail drawer (full ISATVON JSON, citations, dependencies) and Xyflow dependency view per Macro Skill.

### Tests

- [ ] The ISA14 sample manifest (MS01.1) validates as a golden fixture, and a SOW written for it is broken down into a structurally equivalent Sub-Skill.
- [ ] The grid renders empty, malformed and 500-row datasets without crashing.
- [ ] Cyclic Sub-Skill dependencies are rejected.

### Exit Criteria

- [ ] Every approved SOW yields Macro Skills whose Sub-Skills each carry a valid ISATVON 1.4 definition (all 5 loop bounds, decoupled tools, state handoff) with SOW citations.
- [ ] The FDE can review, edit and export the breakdown, and every edit is revalidated and audited.
- [ ] Generate is only possible when all Sub-Skills are valid.

---

# 15. Phase 9 — Sub-Skill JSON Manifest Generation

**Goal:** Convert each Sub-Skill's ISATVON 1.4 definition into its Skill Manifest (exact ISA14 structure + `sbam_build_record`), the single contract for code generation, validation and SRIM, and build the Workflow `schema.sql`.

**Owner:** Dev C · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 8
**Design references:** TD §11.

### Backend

- [ ] Manifest builder (TD §11.1): `skill_metadata` (ids, type `Deterministic_Loopy_MCP`, reusable, version, status, owner, timestamps), `isatvon_definition` (ISA14 shape), `srim_runtime_definition` (tenant defaults), `contracts` (input from S.required_inputs, output from O.json_schema + `state_handoff_id`), `governance` (hash, lifecycle, versioning), `sbam_build_record`.
- [ ] `loop_spec.json` compiler: the 5 bounds compiled into an evaluable form (safe expression grammar).
- [ ] Workflow `schema.sql`: use an uploaded `.sql` as the base, or generate DDL for all V.tables (including handoff tables with an insert-only guard); validate by parsing and applying to ephemeral PostgreSQL (O-16).
- [ ] Canonical JSON (RFC 8785) + SHA-256 hashing utility (shared with certification).
- [ ] Library check (Activity B): every `t_tech_stack.libraries` package must be in the runtime image registry; missing → `DEPENDENCY_FAILED` with guidance.
- [ ] Draft Workflow manifest that references Macro Skills and Sub-Skill manifests (finalized at certification).

### Frontend

- [ ] Manifest Viewer (Monaco, read-only, schema hover, version diff) per Sub-Skill and for the Workflow.

### Tests

- [ ] Same ISATVON → byte-identical manifest and hash; any field change → new hash.
- [ ] A missing dependency produces `DEPENDENCY_FAILED`.

### Exit Criteria

- [ ] Every valid Sub-Skill has a Skill Manifest that validates against the ISA14 structure, with resolved libraries and a deterministic `governance.deterministic_hash`.
- [ ] The Workflow `schema.sql` applies cleanly to an empty PostgreSQL and contains every declared table.

---

# 16. Phase 10 — Temporal Build Orchestration

**Goal:** Implement the durable pipeline (parent Workflow pipeline + per-Sub-Skill build) with signals, retries, correction loops and in-app notifications, so every gate plugs into one backbone.

**Owner:** Dev C · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 9 (can start against stubs in week 3)
**Design references:** TD §13, TD §14.

### Backend

- [ ] `ProjectPipeline` parent Temporal workflow (one per project): ingestion → knowledge → SOW → wait `sow_approved` → ISATVON breakdown → **create the Workflow** → wait `generate` → child builds → Macro certification → Workflow validation & certification → freeze.
- [ ] `SubSkillBuild` child Temporal workflow: manifest → codegen → AST → static/security → test data → Promptfoo suite → CubeSandbox run → certify (with correction loop ≤2).
- [ ] Parallelism: Sub-Skills build concurrently (limit configurable, default 4), respecting `depends_on` order where outputs feed inputs.
- [ ] Signals: `sow_approved`, `generate`, `retry_after_fde_fix`, `rebuild_subskills`, `cancel`. Queries: current stage, progress.
- [ ] Retry policies and timeouts (TD §13.3); idempotent activities; evidence persisted before returning.
- [ ] Notification activity: N-field event → in-app notification → next step.
- [ ] `temporal_runs` mirror table for the UI; search attributes `TenantId`, `ProjectId`, `WorkflowId`, `SubSkillId`.

### Tests

- [ ] Workflow-environment tests for every state path (with mocked activities), time-skipping for waits, replay tests in CI.
- [ ] Worker crash mid-build produces no duplicate evidence and resumes.

### Exit Criteria

- [ ] Every transition in TD §14 is reachable in tests and persisted correctly.
- [ ] Running pipelines survive worker and server restarts without losing or duplicating work.

---

# 17. Phase 11 — Hybrid Code Generation (Jinja2 Skeleton + LLM Body) and Correction

**Goal:** Generate each Sub-Skill's FastMCP tool from its manifest: a deterministic Jinja2 skeleton plus LLM-written logic, with up to two automatic corrections.

**Owner:** Dev C · **Indicative effort:** 2 weeks · **Complexity:** XL · **Depends on:** Phase 10
**Design references:** TD §12.

### Backend

- [ ] Skeleton templates in Git (`templates/fastmcp-loopy/<version>/`, TD §12.3): server registering the **Action tool** and the independent **Fixed Check tool**, Pydantic models from `contracts`, fresh-state reader, append-only state-handoff writer, imports from resolved libraries, logging, and `# >>> LLM_BODY:<name>` protected slots.
- [ ] Template versioning (semver + hash); template version recorded per generated attempt.
- [ ] LLM body generation via `sbam-codegen` (Pydantic AI structured output: action body, check body, state queries, helpers only; no imports outside the allowlist; no retry loops).
- [ ] Assembly: insert bodies into slots → Black format → store bundle + per-file hashes.
- [ ] Correction activity (`sbam-corrector`): given gate failures, may patch the ISATVON JSON (re-manifest) and/or regenerate or edit Python bodies; maximum 2 attempts; then `NEEDS_FDE_REVIEW` with all findings (C-24).
- [ ] Generated-code viewer data (attempts, diffs between attempts, findings).

### Frontend

- [ ] Code Viewer: file tree, Monaco read-only, findings markers, attempt switcher and diff.

### Tests

- [ ] Skeleton golden snapshots per template version; skeleton deterministic for the same manifest.
- [ ] LLM-body fixtures with intentional defects trigger correction, then succeed or stop at 2.
- [ ] Injection attempt in instruction text cannot introduce imports or calls outside the allowlist.

### Exit Criteria

- [ ] Each fixture Sub-Skill produces an assembled FastMCP package (action tool + fixed check tool + handoff writer) whose skeleton matches the template exactly and whose bodies come from the LLM.
- [ ] After two failed corrections, the Sub-Skill always returns to the FDE with complete findings.

---

# 18. Phase 12 — AST and Static Validation Gates

**Goal:** Prove generated code is syntactically valid, conforms to its manifest and passes strict quality and security checks before testing.

**Owner:** Dev C · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 11
**Design references:** TD §12.5–12.6.

### AST validation against the manifest

- [ ] `ast.parse()` for every file (syntax only).
- [ ] Skeleton integrity: AST of the non-slot code equals the rendered skeleton (the LLM cannot alter protected regions).
- [ ] Manifest conformance: action and check tool names, parameters (names, annotations) vs `contracts.input_schema`, outputs vs `contracts.output_schema`, and the check tool returns every identifier used by the gates.
- [ ] Decoupling: the check tool neither imports nor calls the action tool.
- [ ] No manual loops: no `while`/retry loops in tool bodies (iteration is declarative).
- [ ] Handoff: the action path appends a handoff record with all `required_fields`; no UPDATE/DELETE on the handoff table; SQL parameterised only.
- [ ] Import policy: imports ⊆ template allowlist ∪ manifest-resolved libraries; forbidden calls (`eval`, `exec`, `os.system`, `subprocess` unless declared, `pickle`, dynamic `__import__`).

### Static and security checks (toolchain image)

- [ ] Ruff (configured rule set), Black `--check`, MyPy `--strict`.
- [ ] ClamAV scan of the generated bundle; Bandit SAST *(proposed, O-03)*.
- [ ] Import resolution inside the `skill-runtime` image (import every module without running tool logic).
- [ ] Results stored per check with tool versions and config hashes.

### Frontend

- [ ] Build & Test dashboard: gate pipeline per Sub-Skill with pass/fail, findings and attempts.

### Tests

- [ ] One negative fixture per check (syntax error, altered skeleton, wrong parameter type, check imports action, `while` retry loop, missing handoff append, UPDATE on handoff table, f-string SQL, forbidden import, MyPy error, Ruff error, unresolved import).

### Exit Criteria

- [ ] No code reaches the sandbox unless AST conformance and all static/security checks pass on the same code hash.
- [ ] Every result records tool version and configuration, so it can be reproduced.

---

# 19. Phase 13 — Test Data, Promptfoo and CubeSandbox Execution

**Goal:** Prove each Sub-Skill behaves correctly under the ISATVON 1.4 bounded loop on realistic LLM-generated data inside an isolated MicroVM, with at least three passing loop scenarios.

**Owner:** Dev C + Dev A (sandbox host) · **Indicative effort:** 2.5 weeks · **Complexity:** XL · **Depends on:** Phase 12; Linux/KVM server reachable over Netbird
**Design references:** TD §15.

### Test data

- [ ] Test-data generator (`sbam-testgen`): ≥3 loop scenarios per Sub-Skill — PASS_FIRST, ESCALATION and one of PASS_AFTER_RETRY / NO_OP / EDGE — each with inputs, initial fresh-state rows (seed SQL), scripted state changes between passes (retry scenarios), files for FILE refs, mocks for API refs, and the expected terminal state and output (TD §15.1).
- [ ] Datasets versioned, hashed, flagged `synthetic`, and editable or extendable by the FDE before the run.
- [ ] Mock services for sources with network dependencies (generated mock API definitions served by a mock server container, *proposed*).

### Promptfoo

- [ ] Suite builder: deterministic assertions — output schema, terminal state vs success/escalation gates, iterations ≤ `max_iterations`, cycle time ≤ `budget_limit`, handoff rows appended with `required_fields`, expected `ui_updates` sequence; LLM-rubric only where deterministic checks are impossible.
- [ ] Minimum 3 scenarios per Sub-Skill enforced (PASS_FIRST + ESCALATION + one more); all must pass.
- [ ] Results (per case and assertion) stored and pushed as badges to the AG Grid status column.

### CubeSandbox

- [ ] `skill-runtime` image → CubeSandbox template; digest recorded in registry and manifest.
- [ ] Sandbox adapter: create MicroVM, mount bundle + `loop_spec.json` + Workflow `schema.sql` + dataset, start ephemeral PostgreSQL, run each scenario through the **bounded-loop harness** (no-op check → action → fixed check → gates, per pass, within bounds; TD §15.2), collect outputs, harness trace, handoff rows, logs and resources; destroy the VM.
- [ ] Network: egress allowed only to the mock-service network and explicitly allowlisted hosts; all egress logged (C-28).
- [ ] Local fallback `local-docker` mode for laptops, marked `NON_CERTIFYING`.
- [ ] Failure categories: IMPORT_ERROR, RUNTIME_ERROR, TIMEOUT, OOM, OUTPUT_SCHEMA_MISMATCH, ASSERTION_FAILED, INFRA_ERROR. Failures feed the correction loop.

### Tests

- [ ] A known-bad tool fails its suite (meta-test); an egress attempt to a non-allowlisted host is blocked; `local-docker` results cannot certify.

### Exit Criteria

- [ ] Every certification candidate has ≥3 passing loop scenarios (including PASS_FIRST and ESCALATION) executed by the harness in CubeSandbox on the same code hash and dataset hash.
- [ ] Sandbox runs never touch real client systems; all network access is to mocks or allowlisted hosts and is logged.

---

# 20. Phase 14 — Certification, Versioning, Freeze and Export

**Goal:** Certify automatically at three levels (Sub-Skill, Macro Skill, Workflow), version and freeze the result, and produce the exportable manifest package for SRIM.

**Owner:** Dev C (backend) + Dev D (UI) · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 13
**Design references:** TD §11, TD §14.4–14.6.

### Certification

- [ ] Sub-Skill auto-certification: evidence checklist (Skill Manifest valid, libraries resolved, all AST checks, static/security, schema SQL, ≥3 loop scenarios pass in certifying CubeSandbox) on one code hash → `CERTIFIED`; manifest `status` and `governance.lifecycle.state` set to `APPROVED`, `immutable_after_approval: true`.
- [ ] Macro Skill certification: all its Sub-Skills certified + intra-macro dependency check (each Sub-Skill's input schema is satisfied by upstream outputs or declared sources).
- [ ] Workflow validation and certification: all Macro Skills certified, complete Xyflow DAG, no dangling dependencies, workflow-level smoke run chaining Sub-Skills on synthetic data *(proposed)*, Workflow manifest assembled and hashed.
- [ ] Reuse: unchanged Sub-Skills (same manifest hash, template version, runtime digest and suite hash) carry their certification into the new version.

### Versioning and freeze

- [ ] MAJOR.0.PATCH versioning (TD §11.4): major on new approved SOW version; patch on Sub-Skill-level rebuilds under the same SOW.
- [ ] Freeze: DB immutability trigger on certified records; storage files marked read-only/immutable (S3 Object Lock later); read-back hash verification.
- [ ] No revocation; changes always create a new version.

### Export (MVP handover)

- [ ] Export package: Workflow manifest + Skill Manifests (ISA14 format) + code bundles + `loop_spec.json` files + `schema.sql` + evidence index + checksums (zip), downloadable by the FDE.
- [ ] SRIM handover API kept as a documented interface stub only **[FUTURE]**.

### Frontend

- [ ] Certification view: tree Workflow → Macro → Sub-Skill with status, evidence links, version and hash; Version History with diff and lineage; Export button.

### Tests

- [ ] Partial pass (one Sub-Skill fails) leaves its Macro and the Workflow uncertified.
- [ ] A change to one Sub-Skill after freeze creates version 1.0.1 with the others reused.
- [ ] Attempts to modify frozen records via API, SQL or file system fail.

### Exit Criteria

- [ ] A Workflow is certified only when every Macro Skill and Sub-Skill is certified on current hashes.
- [ ] Frozen versions are immutable, and recomputing any frozen manifest hash reproduces it.
- [ ] The FDE can download a complete, checksum-verified export package.

---

# 21. Phase 15 — End-to-End Testing, Security and Performance

**Goal:** Prove the full SBAM journey works and fails safely, and meets the targets, in a production-like Docker environment.

**Owner:** All (Dev D leads E2E) · **Indicative effort:** 1.5 weeks (starts in parallel from Phase 8) · **Complexity:** L · **Depends on:** Phases 2–14
**Design references:** TD §19.

## Functional

- [ ] Automate scenarios E01–E20 (TD §19.2) with Playwright on the staging Compose stack + CubeSandbox host.
- [ ] Rework paths: SOW edit/re-approval, scope change, correction loop, FDE fix after 2 failed corrections.

## Security

- [ ] Cross-tenant/project IDOR suite; Keycloak token abuse; prompt-injection documents; template/code-injection via ISATVON fields.
- [ ] Frozen-record tamper tests; ClamAV EICAR; sandbox egress tests; Trivy image scan with no CRITICAL findings.

## Performance

- [ ] 10 concurrent users; 4 parallel Sub-Skill builds; 20 × 15 MB ingestion; grid with 500 Sub-Skills.

## LLM quality

- [ ] Golden sets for OCR, SOW, ISATVON, codegen, testgen and correction; regression gate on prompt or model change.

### Exit Criteria

- [ ] All E2E scenarios pass in five consecutive CI runs on staging.
- [ ] No unresolved critical/high security findings.
- [ ] Release targets in Section 28 are met.

---

# 22. Phase 16 — Environments, Operations, UAT and Go-Live

**Goal:** Run SBAM reliably in dev, staging and production (Docker Compose on VMs), with observability, backups and UAT sign-off.

**Owner:** Dev A + all · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 15
**Design references:** TD §17, TD §20.

## Environments and release

- [ ] Compose files per environment (dev, staging, prod) using the same image digests; promotion only by digest.
- [ ] Reverse proxy with TLS (Caddy/Traefik, *proposed*) as the only public entry; Netbird mesh for SSH, Temporal UI, Grafana, Langfuse and the CubeSandbox host.
- [ ] Migration job before rollout; rollback runbook (previous digests + forward-fix policy); graceful worker draining.
- [ ] GitHub Actions deploy workflows (manual approval for production).

## Observability

- [ ] OTEL collector → Grafana LGTM (*proposed*) dashboards: API, Temporal queues, gate pass rates, sandbox, LLM latency/cost.
- [ ] Langfuse projects per environment; PostHog events (funnel: workflow created → frozen; gate failures; corrections).
- [ ] In-app alerts for failures and pending actions (C-43); operator alerts in Grafana.

## Backup and recovery

- [ ] Nightly PostgreSQL dumps (all databases) + storage volume snapshot + Cognee volume snapshot; retention 30 days *(proposed)*.
- [ ] Restore drill:

```text
Failure → Restore PostgreSQL → Restore storage + Cognee volumes → Start Temporal + workers
        → Re-verify hashes of frozen manifests → Smoke tests → Measure RPO/RTO
```

## UAT

- [ ] Admin UAT (tenant, project, masters, RBAC) and FDE UAT (full flow to export) on staging with real (non-sensitive) project data.
- [ ] Runbooks: stuck Temporal workflow, CubeSandbox host down, OpenRouter outage, ClamAV signature failure, disk full.

### Exit Criteria

- [ ] Restore drill succeeds within RPO 24 h / RTO 8 h, and all frozen manifests re-verify.
- [ ] Admin and FDE UAT are signed off with no open critical/high defects.
- [ ] The first real Workflow is certified, frozen and exported in production.

---

# 23. Task Breakdown

Paths follow the monorepo in TD §6: `api:` = `server/api/sbam_api/…`, `wk:` = `server/workers/sbam_workers/…`, `web:` = `client/src/…`, `ct:` = `server/contracts/…`, `tpl:` = `server/templates/…`, `ops:` = `compose/…`. **Dev**: A platform/DevOps/security, B knowledge/LLM, C build pipeline (Temporal, codegen, validation, sandbox), D frontend. Priority: P0 critical path, P1 MVP, P2 after MVP.

| ID       | Phase  | Component            | Description                                                                                                                                                                                                                        | Deps          | Files / modules                                    | API                   | DB                                                         | UI                     | Tests                                      | Acceptance                               | Dev | Pri |
| -------- | ------ | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | -------------------------------------------------- | --------------------- | ---------------------------------------------------------- | ---------------------- | ------------------------------------------ | ---------------------------------------- | --- | --- |
| SBAM-001 | 0      | Contracts            | ISATVON 1.4 Sub-Skill + Macro Skill models (5 loop bounds, safe gate-expression grammar), JSON Schema                                                                                                                              | ISA14         | `ct:isatvon14`                                   | `/schemas/isatvon`  | —                                                         | —                     | ISA14 sample fixture                       | Sample validates                         | C   | P0  |
| SBAM-002 | 0      | Contracts            | Skill Manifest (ISA14 structure +`sbam_build_record`), Workflow manifest, `loop_spec` model, RFC 8785 + SHA-256 utility                                                                                                        | 001           | `ct:manifest`, `ct:hashing`                    | `/schemas/manifest` | —                                                         | —                     | Property tests                             | Deterministic hash                       | C   | P0  |
| SBAM-003 | 0      | Contracts            | Six master schemas from MSC (frozen BAM 0.2 columns), RAG Reference schema, stable-ID/status extraction rules; ISATVON-derived`SowDocument`                                                                                      | MSC, ISA14    | `ct:masters`, `ct:sow`                         | `/schemas/masters`  | —                                                         | —                     | MSC rows validate                          | Schemas load MSC                         | B   | P0  |
| SBAM-004 | 0      | Spikes               | Cognee/OKF, CubeSandbox, hybrid codegen, Promptfoo, Keycloak spikes                                                                                                                                                                | —            | `docs/spikes`                                    | —                    | —                                                         | —                     | Reports                                    | Feasible / decided                       | All | P0  |
| SBAM-005 | 1      | Repo                 | Monorepo, tooling, contract → TS generation, ADRs                                                                                                                                                                                 | 004           | root,`ct:`, `scripts/gen-types`                | —                    | —                                                         | Types                  | CI drift check                             | Builds clean                             | A   | P0  |
| SBAM-006 | 1      | API base             | App factory, settings, errors, correlation, logging, OTEL, health                                                                                                                                                                  | 005           | `api:core`                                       | `/health/*`         | —                                                         | —                     | Unit                                       | Ready green                              | A   | P0  |
| SBAM-007 | 1      | DB                   | Alembic baseline, conventions, base repository with tenant scope                                                                                                                                                                   | 006           | `api:db`, `api:migrations`                     | —                    | baseline                                                   | —                     | Migration test                             | Up/down clean                            | A   | P0  |
| SBAM-008 | 1      | Storage              | StorageBackend + LocalDiskBackend + S3 stub                                                                                                                                                                                        | 006           | `api:storage`                                    | —                    | `artifacts`                                              | —                     | Unit + integration                         | Content-addressed writes                 | A   | P0  |
| SBAM-009 | 1      | Docker               | Dockerfiles (web, api, worker, toolchain, promptfoo, skill-runtime)                                                                                                                                                                | 005           | `ops:docker/*`                                   | —                    | —                                                         | —                     | hadolint, Trivy                            | Non-root, pinned                         | A   | P0  |
| SBAM-010 | 1      | Docker               | Compose profiles, init jobs, Makefile,`.env.example`, README                                                                                                                                                                     | 009           | `ops:compose/*`                                  | —                    | init DBs                                                   | —                     | Smoke                                      | `make up` healthy                      | A   | P0  |
| SBAM-011 | 1      | CI                   | GitHub Actions pipeline, GHCR push, scans, SBOM                                                                                                                                                                                    | 009           | `.github/workflows`                              | —                    | —                                                         | —                     | Pipeline                                   | Blocks failures                          | A   | P0  |
| SBAM-012 | 1      | Temporal             | Client, worker bootstrap, queues, interceptor,`temporal_runs` mirror                                                                                                                                                             | 006           | `wk:bootstrap`, `api:temporal`                 | —                    | `temporal_runs`                                          | —                     | Sample workflow                            | Runs on all queues                       | C   | P0  |
| SBAM-013 | 1      | Web base             | Next.js app,`(admin)`/`(fde)` groups, Refine, Auth.js Keycloak, UI kit                                                                                                                                                         | 005           | `web:app`, `web:providers`, `web:components` | —                    | —                                                         | Shell                  | Vitest                                     | Login works                              | D   | P0  |
| SBAM-014 | 2      | Identity             | Keycloak realm, clients, roles, tenant mapper, realm export                                                                                                                                                                        | 010           | `ops:keycloak`                                   | —                    | —                                                         | —                     | Login tests                                | Tokens carry tenant                      | A   | P0  |
| SBAM-015 | 2      | AuthZ                | JWT validation, authz chain, tenant-scoped repos, RBAC APIs, audit                                                                                                                                                                 | 014, 007      | `api:auth`, `api:tenancy`, `api:audit`       | §8.2                 | `tenants`, `users`, `project_members`, `audit_log` | Users & Roles          | RBAC + IDOR                                | No cross-tenant leak                     | A   | P0  |
| SBAM-016 | 3      | Projects             | Project CRUD, FDE assignment, context notes                                                                                                                                                                                        | 015           | `api:projects`                                   | §8.3                 | `projects`                                               | Admin projects         | API                                        | CRUD + isolation                         | A   | P0  |
| SBAM-017 | 3      | Masters              | Generic masters engine, versions, scope, snapshot API, outdated flag; frozen-structure workbook import/export (MSC layout); stable-ID/status extraction; RAG Reference → Cognee mapping; Filetype read-only + loader/route config | 003, 016      | `api:masters`, `api:masters/xlsx`              | §8.3                 | `master_*`                                               | —                     | Round-trip MSC; structure-change rejection | Import = export                          | A   | P0  |
| SBAM-072 | 3      | Master documents     | Admin context files: upload, ClamAV, route by Filetype default route, Admin override, parse, add to master context                                                                                                                 | 017, 020      | `api:masters/documents`                          | §8.3                 | `master_documents`                                       | Master documents panel | Routing per intent class                   | Routed + ingested                        | A   | P1  |
| SBAM-018 | 3      | Admin UI             | Projects, masters editors (schema forms), README editor, users                                                                                                                                                                     | 016, 017, 013 | `web:(admin)/*`                                  | §8.2–8.3            | —                                                         | Admin portal           | E2E                                        | Admin flow done                          | D   | P0  |
| SBAM-019 | 4 | Project pipeline | Project workspace API, `ProjectPipeline` start on Start processing, pipeline state + dashboard | 016, 012 | `api:projects/pipeline` | §8.4 | `projects.pipeline_state` | Project overview | API | Pipeline starts for assigned FDE only | C | P0 |
| SBAM-020 | 4      | Upload               | tusd + hooks, limits, allowlist, magic bytes, dedupe, ClamAV                                                                                                                                                                       | 008, 019      | `api:documents`, `ops:tusd`, `ops:clamav`    | §8.5                 | `documents`, `document_versions`                       | —                     | EICAR, limits                              | Unsafe files rejected                    | B   | P0  |
| SBAM-021 | 4      | Parsing              | `DocumentIngestion` workflow; loaders per Filetype entry (MVP set, TD §5.2), archives, normalization, LLM OCR                                                                                                                   | 020, 017      | `wk:ingest/loaders`                              | §8.5                 | `ingestion_runs`                                         | —                     | Fixture per MVP type                       | Reaches PARSED                           | B   | P0  |
| SBAM-022 | 4      | Lifecycle            | Soft delete, restore, 90-day purge schedule                                                                                                                                                                                        | 020           | `api:documents`, `wk:purge`                    | §8.5                 | `deleted_at`                                             | Recycle bin            | Time-skip test                             | Purged after 90 d                        | B   | P1  |
| SBAM-023 | 4 | FDE UI | Project workspace (assigned projects, overview timeline), Uppy upload, processing status, recycle bin | 019–022, 013 | `web:(fde)/projects/*` | §8.4–8.5 | — | 4 screens | E2E | Upload journey | D | P0 |
| SBAM-024 | 5      | Knowledge            | Cognee adapter (default stores), per-project dataset, Bifrost routing                                                                                                                                                              | 004, 021      | `wk:knowledge/cognee`                            | —                    | `knowledge_versions`                                     | —                     | Isolation                                  | Isolated datasets                        | B   | P0  |
| SBAM-025 | 5      | OKF                  | OKF Bundle build + validation + coverage                                                                                                                                                                                           | 024           | `wk:knowledge/okf`                               | §8.6                 | `artifacts`                                              | —                     | Round-trip                                 | 100% coverage                            | B   | P0  |
| SBAM-026 | 5      | Graph API            | Graph read/search/node-detail endpoints                                                                                                                                                                                            | 024           | `api:knowledge`                                  | §8.6                 | —                                                         | —                     | API                                        | Paged graph                              | B   | P1  |
| SBAM-027 | 5      | Explorer UI          | Read-only Xyflow knowledge explorer + coverage panel                                                                                                                                                                               | 026           | `web:(fde)/…/knowledge`                         | §8.6                 | —                                                         | Explorer               | Component                                  | Explore, no edit                         | D   | P1  |
| SBAM-028 | 6      | LLM layer            | Bifrost config (aliases → OpenRouter), Pydantic AI wrapper, prompt registry,`llm_calls`, Langfuse                                                                                                                               | 010           | `wk:llm`, `ops:bifrost`                        | —                    | `llm_calls`                                              | —                     | Unit                                       | Every call traced                        | B   | P0  |
| SBAM-029 | 6      | SOW gen              | `SowDocument`, one-shot generation activity, re-ask, versions, diff                                                                                                                                                              | 025, 028, 003 | `wk:sow`, `api:sow`                            | §8.7                 | `sows`, `sow_versions`                                 | —                     | Golden set                                 | Cited valid SOW                          | B   | P0  |
| SBAM-030 | 6      | SOW export           | PDF/DOCX/MD export                                                                                                                                                                                                                 | 029           | `wk:exports`                                     | `…/export`         | `artifacts`                                              | Export menu            | Open files                                 | Exports valid                            | B   | P1  |
| SBAM-031 | 6      | SOW UI               | TipTap editor, citation panel, versions/diff, regenerate                                                                                                                                                                           | 029, 013      | `web:(fde)/…/sow`                               | §8.7                 | —                                                         | SOW screen             | E2E                                        | Edit → version                          | D   | P0  |
| SBAM-032 | 7      | Approval             | Approve (hash-bound), lock, pipeline signal                                                                                                                                                                                        | 029, 015      | `api:sow/approval`                               | §8.7                 | `sow_versions.approved_*`                                | Approve button         | API                                        | Gate enforced                            | B   | P0  |
| SBAM-033 | 7      | Scope change         | New SOW version, impact analysis via`sow_refs`, invalidation                                                                                                                                                                     | 032, 035      | `api:sow/impact`                                 | `…/impact`         | `sub_skills.state`                                       | Impact preview         | Unit + E2E                                 | Only affected rebuilt                    | B   | P1  |
| SBAM-034 | 8      | Extraction           | Two-pass Macro (MSnn) → Sub-Skill (MSnn.m) ISATVON 1.4 extraction + validation (TD §10.4)                                                                                                                                        | 032, 001      | `wk:isatvon`                                     | §8.8                 | `macro_skills`, `sub_skills`                           | —                     | ISA14 golden                               | All bounds + decoupled tools + citations | B   | P0  |
| SBAM-035 | 8      | ISATVON API          | Edit/delete/reorder, regenerate macro/sub, field audit                                                                                                                                                                             | 034           | `api:isatvon`                                    | §8.8                 | same                                                       | —                     | API                                        | Invalid edits rejected                   | B   | P0  |
| SBAM-036 | 8      | Excel                | openpyxl export                                                                                                                                                                                                                    | 034           | `wk:exports/isatvon_xlsx`                        | `…/export.xlsx`    | `artifacts`                                              | Export                 | Unit                                       | Matches grid                             | B   | P1  |
| SBAM-037 | 8      | Grid UI              | AG Grid workbench (tree, schema columns, editors, safeguards, drawer, DAG)                                                                                                                                                         | 035, 013      | `web:(fde)/…/isatvon`                           | §8.8                 | —                                                         | Workbench              | Component (500 rows), E2E                  | Never crashes; Generate gated            | D   | P0  |
| SBAM-038 | 9      | Manifest             | Skill Manifest builder (ISA14 shape),`loop_spec.json` compiler, library check vs registry, Workflow draft manifest                                                                                                               | 002, 035, 046 | `wk:build/manifest`                              | §8.9                 | `sub_skill_manifests`, `workflow_manifests`            | —                     | Determinism                                | Hash stable                              | C   | P0  |
| SBAM-070 | 9      | Schema SQL           | Workflow`schema.sql`: uploaded base or generated DDL for V.tables, insert-only handoff guard, parse + apply validation                                                                                                           | 038           | `wk:build/schema_sql`                            | `W/schema-sql`      | `workflow_schemas`                                       | Schema view            | Apply to empty PG                          | All tables present                       | C   | P0  |
| SBAM-039 | 9      | Manifest UI          | Monaco manifest viewer + diff                                                                                                                                                                                                      | 038           | `web:(fde)/…/manifests`                         | §8.9                 | —                                                         | Viewer                 | Component                                  | Read-only                                | D   | P1  |
| SBAM-040 | 10     | Orchestration        | `ProjectPipeline` + `SubSkillBuild` Temporal workflows, signals, parallelism, retries                                                                                                                                         | 012, 038      | `wk:pipeline`                                    | §8.10                | `temporal_runs`, state cols                              | Timeline               | WF env + replay                            | All paths                                | C   | P0  |
| SBAM-041 | 10     | Notifications        | N-field events → in-app notifications; notification centre API                                                                                                                                                                    | 040           | `api:notifications`                              | §8.13                | `notifications`                                          | Bell + inbox           | Unit                                       | Each step notifies                       | D   | P1  |
| SBAM-042 | 11     | Templates            | `fastmcp-loopy` skeleton templates: server, action tool, fixed check tool, models, state reader, handoff writer, protected slots, versioning                                                                                     | 002           | `tpl:fastmcp-loopy/*`                            | —                    | `code_templates`                                         | —                     | Snapshots                                  | Deterministic skeleton                   | C   | P0  |
| SBAM-043 | 11     | Codegen              | LLM body generation, assembly, Black, bundle storage                                                                                                                                                                               | 042, 028      | `wk:build/codegen`                               | §8.11                | `code_attempts`                                          | —                     | Fixtures                                   | Assembled tool                           | C   | P0  |
| SBAM-044 | 11     | Correction           | Correction activity (ISATVON patch + Python edit), max 2, FDE review                                                                                                                                                               | 043, 047      | `wk:build/correction`                            | `…/retry`          | `code_attempts`                                          | Findings + retry       | WF tests                                   | Stops at 2                               | C   | P0  |
| SBAM-045 | 11     | Code UI              | Code viewer, attempts, diffs, findings markers                                                                                                                                                                                     | 043           | `web:(fde)/…/code`                              | §8.11                | —                                                         | Code viewer            | Component                                  | Findings visible                         | D   | P1  |
| SBAM-046 | 12     | Registry             | Runtime dependency registry synced from`skill-runtime` image build                                                                                                                                                               | 009           | `ops:docker/skill-runtime`, `api:registry`     | `/registry`         | `runtime_registry`                                       | Admin view             | Build test                                 | Digest pinned                            | A   | P0  |
| SBAM-047 | 12     | AST gate             | Syntax, skeleton integrity, conformance, decoupling, no manual loops, handoff, import policy, SQL safety (TD §12.5)                                                                                                               | 043           | `wk:build/ast_gate`                              | —                    | `gate_results`                                           | —                     | Negative fixture per check                 | All caught                               | C   | P0  |
| SBAM-048 | 12     | Static gate          | Ruff, Black, MyPy strict, ClamAV, Bandit, import resolution                                                                                                                                                                        | 009, 043      | `wk:build/static_gate`                           | —                    | `gate_results`                                           | —                     | Negative fixtures                          | Versions recorded                        | C   | P0  |
| SBAM-049 | 12     | Build UI             | Build & Test dashboard (gates per Sub-Skill)                                                                                                                                                                                       | 047, 048      | `web:(fde)/…/build`                             | §8.10–8.12          | —                                                         | Dashboard              | E2E                                        | Live gate status                         | D   | P0  |
| SBAM-050 | 13     | Test data            | `sbam-testgen` loop scenarios (PASS_FIRST, ESCALATION + one more), seed state, state scripts, files, mocks; FDE edit                                                                                                             | 038, 028      | `wk:test/testgen`                                | §8.12                | `test_datasets`, `test_cases`                          | Test panel             | Golden                                     | ≥3 loop scenarios                       | C   | P0  |
| SBAM-071 | 13     | Loop harness         | Bounded-loop harness (no-op → action → fixed check → gates, max_iterations, budget, ui_updates, handoff capture) in`skill-runtime`                                                                                            | 038           | `services/workers/…/test/harness`               | —                    | `sandbox_runs`                                           | Harness trace          | Unit + ISA14 sample                        | Semantics per`loop_spec`               | C   | P0  |
| SBAM-051 | 13     | Promptfoo            | Suite builder (deterministic + rubric), runner, results, grid badges                                                                                                                                                               | 050           | `wk:test/promptfoo`                              | §8.12                | `test_runs`                                              | Badges                 | Meta-test                                  | All pass required                        | C   | P0  |
| SBAM-052 | 13     | Sandbox host         | CubeSandbox on Linux/KVM, Netbird, template from skill-runtime, egress policy                                                                                                                                                      | 046           | `ops:sandbox-host`                               | —                    | —                                                         | —                     | Egress test                                | Isolated + controlled                    | A   | P0  |
| SBAM-053 | 13     | Sandbox adapter      | cubesandbox + local-docker (non-certifying) adapter, execution, failure categories                                                                                                                                                 | 052, 051      | `wk:test/sandbox`                                | —                    | `sandbox_runs`                                           | —                     | Integration                                | Certifying runs only on host             | C   | P0  |
| SBAM-054 | 13     | Mock services        | Mock API server container for networked sources                                                                                                                                                                                    | 050           | `ops:mock-api`, `wk:test/mocks`                | —                    | —                                                         | —                     | Integration                                | Tools reach mocks                        | C   | P1  |
| SBAM-055 | 14     | Certification        | Sub-Skill checklist, Macro certification, Workflow validation/certification, reuse                                                                                                                                                 | 053, 040      | `wk:certify`, `api:certification`              | §8.14                | `certifications`                                         | —                     | E2E                                        | Only full evidence certifies             | C   | P0  |
| SBAM-056 | 14     | Versioning & freeze  | MAJOR.0.PATCH, immutability triggers, read-only files, hash verify                                                                                                                                                                 | 055           | `wk:certify/freeze`, `api:migrations`          | §8.14                | triggers                                                   | —                     | Tamper tests                               | Immutable                                | C   | P0  |
| SBAM-057 | 14     | Export               | Export package (zip, checksums); SRIM handover stub                                                                                                                                                                                | 056           | `wk:exports/package`                             | `…/export-package` | `artifacts`                                              | Export                 | Checksum test                              | Package verifies                         | C   | P0  |
| SBAM-058 | 14     | Cert UI              | Certification tree, version history, lineage, export                                                                                                                                                                               | 055–057      | `web:(fde)/…/certification`                     | §8.14                | —                                                         | 2 screens              | E2E                                        | Full lineage                             | D   | P0  |
| SBAM-059 | 15     | Security             | Threat model, IDOR, injection corpora, tamper, egress, scans                                                                                                                                                                       | 057           | `tests/security`                                 | all                   | —                                                         | —                     | Suite                                      | No critical                              | A   | P0  |
| SBAM-060 | 15     | E2E                  | Playwright E01–E20 on staging                                                                                                                                                                                                     | 058           | `tests/e2e`                                      | —                    | —                                                         | all                    | Playwright                                 | Green ×5                                | D   | P0  |
| SBAM-061 | 15     | Perf + LLM quality   | Load tests and golden-set regression gates                                                                                                                                                                                         | 060           | `tests/perf`, `tests/llm`                      | —                    | —                                                         | —                     | k6, golden                                 | Targets met                              | B   | P1  |
| SBAM-062 | 16     | Observability        | OTEL collector, Grafana LGTM, Langfuse, PostHog, dashboards, alerts                                                                                                                                                                | 010           | `ops:observability`                              | —                    | —                                                         | —                     | Alert drill                                | Correlated traces                        | A   | P1  |
| SBAM-063 | 16     | Environments         | dev/staging/prod Compose, reverse proxy, Netbird, deploy workflows, rollback                                                                                                                                                       | 011           | `ops:env/*`                                      | —                    | —                                                         | —                     | Deploy drill                               | Repeatable deploys                       | A   | P0  |
| SBAM-064 | 16     | Backup/DR            | Backups (PG, storage, Cognee), restore drill                                                                                                                                                                                       | 063           | `ops:backup`                                     | —                    | —                                                         | —                     | Drill                                      | RPO/RTO met                              | A   | P1  |
| SBAM-065 | 16     | UAT                  | UAT scripts, runbooks, readiness review                                                                                                                                                                                            | 059–064      | `docs/runbooks`                                  | —                    | —                                                         | —                     | UAT                                        | Sign-off                                 | All | P0  |
| SBAM-066 | Future | Approval integration | Email/other approval channel for SOW                                                                                                                                                                                               | 032           | `api:sow/approval_channels`                      | new                   | —                                                         | —                     | —                                         | —                                       | —  | P2  |
| SBAM-067 | Future | Ingestion channels   | API/webhook/cron/email/watchdog ingestion                                                                                                                                                                                          | 021           | `api:ingestion/channels`                         | new                   | —                                                         | —                     | —                                         | —                                       | —  | P2  |
| SBAM-068 | Future | SRIM handover        | Handover API, ack, environment switch                                                                                                                                                                                              | 057           | `api:handover`                                   | new                   | —                                                         | —                     | —                                         | —                                       | —  | P2  |
| SBAM-069 | Future | AWS                  | S3Backend, AWS hosting, Object Lock, manifest signing                                                                                                                                                                              | 008           | `api:storage/s3`, `ops:aws`                    | —                    | —                                                         | —                     | —                                         | —                                       | —  | P2  |

---

# 24. Team Allocation and Parallel Development Plan

## 24.1 Streams (4 developers)

| Dev         | Stream                               | Owns                                                                                                                                                                                           |
| ----------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A** | Platform, DevOps, identity, security | Repo, Docker/Compose, CI, Keycloak, tenancy/RBAC, storage, projects/masters API, runtime image, CubeSandbox host, Netbird, observability, environments, backup, security tests                 |
| **B** | Knowledge and LLM                    | Ingestion/parsing/OCR, Cognee, OKF, Bifrost/OpenRouter layer, SOW generation/export/approval/impact, ISATVON extraction and APIs, LLM golden sets                                              |
| **C** | Build pipeline                       | Contracts (ISATVON/manifest), Temporal pipeline, manifest builder, templates, hybrid codegen, correction, AST/static gates, test data, Promptfoo, sandbox adapter, certification/freeze/export |
| **D** | Frontend                             | Next.js shell, Admin portal, all FDE screens (upload, processing, explorer, SOW, grid, manifests, code, build/test, certification, versions, notifications), E2E suite                         |

## 24.2 Indicative timeline *(estimate, 4 developers in parallel)*

```text
Week:            1    2    3    4    5    6    7    8    9    10   11   12   13   14   15
Phase 0          ███
Dev A  P1/P2/P3       ████ ████ ███                                  (then: runtime image, sandbox host,
                                                                       observability, environments)
Dev A  sandbox host + Netbird            ████ ████                     P16 ████ ████
Dev B  P4 ingest            ████ ██
Dev B  P5 knowledge                  ██ ████ ██
Dev B  P6/P7 SOW + approval                    ████ ███
Dev B  P8 ISATVON extraction                            ████ ████
Dev C  contracts + Temporal skeleton  ███ ████ ███
Dev C  P9 manifest + P11 codegen (fixtures first)  ████ ████ ████
Dev C  P12 gates + P13 test/sandbox                            ████ ████ ████
Dev C  P14 certification                                                 ████ ███
Dev D  shell + Admin portal       ████ ████
Dev D  FDE screens (behind each API)        ████ ████ ████ ████ ████ ████
Dev D  P15 E2E                                                       ████ ████ ███
Go-live readiness                                                               ████
```

Indicative MVP duration: **about 15 weeks** with 4 developers, provided the Phase 0 inputs arrive in week 1 and the KVM host is ready by week 4. Track C starts code generation against fixture manifests (from the ISATVON example) before extraction is finished, which removes the biggest serial dependency.

## 24.3 Critical path

```text
Phase 0 inputs (ISATVON example, masters schema, SOW template)
  → Contracts (SBAM-001/002) → Temporal skeleton (012, 040)
  → Ingestion (020/021) → Cognee + OKF (024/025) → SOW (029) → Approval (032)
  → ISATVON extraction (034/035) → Manifest (038)
  → Codegen (042/043) → Gates (047/048) → Test data + Promptfoo + CubeSandbox (050–053)
  → Certification + freeze + export (055–057) → E2E (060) → UAT (065)
```

## 24.4 Integration points

| IP   | When        | Demo                                                                                                               |
| ---- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| IP-1 | End week 4 | Admin creates a project with masters; FDE opens the project and uploads; documents PARSED |
| IP-2 | End week 7  | Knowledge graph + OKF Bundle → SOW generated, edited, approved                                                    |
| IP-3 | End week 10 | ISATVON breakdown in the grid → manifests → code generated and passing AST/static gates (fixture-driven earlier) |
| IP-4 | End week 13 | Full build on CubeSandbox with Promptfoo → Sub/Macro/Workflow certified → frozen → exported                     |

---

# 25. MVP Definition

## 25.1 MVP must deliver end to end

| #  | Capability                              | MVP scope                                                                                                                                                                      |
| -- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1  | Two portals with Keycloak, multi-tenant | Admin and FDE logins; tenant isolation                                                                                                                                         |
| 2  | Admin projects, masters and RBAC        | Five masters + README/Governance (per provided schema), FDE assignment                                                                                                         |
| 3 | Project workspace | FDE opens an assigned project and starts the pipeline (SBAM creates the Workflow from the approved SOW) |
| 4  | Upload                                  | Uppy, ≤15 MB × ≤20 files, ClamAV, soft delete/90-day recovery                                                                                                               |
| 5  | Processing                              | Parsing of the MVP types + basic LLM OCR                                                                                                                                       |
| 6  | Knowledge                               | Cognee graph (with Admin context) + OKF Bundle + read-only explorer                                                                                                            |
| 7  | SOW                                     | One-shot cited SOW, TipTap edit, versions, PDF/DOCX/MD export                                                                                                                  |
| 8  | SOW approval                            | FDE approval gate; scope-change versioning with impact analysis                                                                                                                |
| 9  | ISATVON                                 | Macro → Sub-Skill breakdown, ISATVON per Sub-Skill, grid editing, Excel export                                                                                                |
| 10 | Manifests                               | Sub-Skill manifests + Workflow manifest, dependency check                                                                                                                      |
| 11 | Code                                    | Hybrid Jinja2 + LLM FastMCP code, 2 correction attempts                                                                                                                        |
| 12 | Validation                              | AST vs manifest, Ruff, Black, MyPy strict, ClamAV (+ Bandit)                                                                                                                   |
| 13 | Testing                                 | LLM test data, ≥3 Promptfoo tests, CubeSandbox with controlled egress                                                                                                         |
| 14 | Certification                           | Automatic Sub-Skill → Macro → Workflow, reuse of unchanged parts                                                                                                             |
| 15 | Freeze and versions                     | MAJOR.0.PATCH, immutable, hash-verified                                                                                                                                        |
| 16 | Export                                  | Downloadable frozen manifest package (MVP handover to SRIM)                                                                                                                    |
| 17 | Platform                                | Temporal, PostgreSQL, local storage adapter, Bifrost/OpenRouter, OTEL + Langfuse + PostHog, in-app notifications, audit table, Docker Compose (4 environments), GitHub Actions |

## 25.2 After MVP

Email/other approval integrations; API/webhook/cron/email/watchdog ingestion; advanced OCR; AWS hosting and S3 (+ Object Lock); SRIM handover API and acknowledgement; manifest signing; compliance controls; scaling beyond 10 concurrent users.

---

# 26. Definition of Done

## 26.1 A Sub-Skill is certified only when

- [ ] Its Workflow's SOW version is approved by an FDE (hash-bound approval).
- [ ] Its ISATVON 1.4 definition is valid: all elements, the 5 loop bounds, decoupled action/check tools, state handoff and SOW citations.
- [ ] Its Skill Manifest matches the ISA14 structure and every library resolves in the runtime registry.
- [ ] Code (action tool, fixed check tool, handoff writer) was generated from a pinned skeleton template plus LLM bodies, within ≤2 corrections.
- [ ] AST validation passed: syntax, skeleton integrity, conformance, decoupling, no manual loops, handoff, import policy, SQL safety.
- [ ] Ruff, Black, MyPy strict, ClamAV (and Bandit) passed on the same code hash.
- [ ] At least 3 loop scenarios (including PASS_FIRST and ESCALATION) passed through the harness in a certifying CubeSandbox run on a hashed synthetic dataset, with handoff rows appended.
- [ ] A certification record exists, and the manifest has `status` = `APPROVED`, a version and `deterministic_hash`.

## 26.2 A Macro Skill is certified only when

- [ ] Every one of its Sub-Skills is certified on current hashes.
- [ ] Its internal dependency graph is complete and acyclic.

## 26.3 A Workflow is certified and frozen only when

- [ ] Every Macro Skill is certified.
- [ ] Workflow validation passed (DAG complete, no dangling inputs, `schema.sql` complete, workflow-level smoke run).
- [ ] The Workflow manifest is assembled, versioned (MAJOR.0.PATCH), hashed and frozen.
- [ ] All referenced artifacts are immutable and hash-verified.
- [ ] The export package is generated and its checksums verify.

## 26.4 A feature is Done only when

- [ ] It references the related decision/requirement IDs (C-xx / AR-xx).
- [ ] The API is in OpenAPI and the TS types are regenerated.
- [ ] Tenant/project authorization is implemented and covered by RBAC/IDOR tests.
- [ ] Server-side Pydantic validation is implemented.
- [ ] The Alembic migration is added and reversible.
- [ ] State changes go through the state machine service and write the audit table.
- [ ] Errors map to the failure matrix (TD §18) with user-visible messages.
- [ ] OTEL traces/logs carry tenant, project, workflow and correlation IDs; LLM calls are traced in Langfuse.
- [ ] Unit + integration tests exist; Temporal tests for workflow changes; E2E for user journeys.
- [ ] Loading, empty, error and forbidden UI states are implemented.
- [ ] The Docker image builds and scans clean, and the Compose stack starts.
- [ ] Docs and ADRs are updated, CI is green and the staging smoke test passes.

---

# 27. Recommended Delivery Order

The recommended execution order is:

```text
0. Architecture, Contracts and Inputs Baseline
        ↓
1. Repository Foundation and Containerized Dev Stack
        ↓
2. Identity, Multi-Tenancy and RBAC
        ↓
3. Admin Portal: Projects and Masters
        ↓
4. Project Workspace and Document Ingestion
        ↓
5. Knowledge Construction (Cognee, OKF Bundle, Graph Explorer)
        ↓
6. SOW Generation, Editing and Export
        ↓
7. SOW Approval (HITL) and Scope-Change Versioning
        ↓
8. ISATVON Breakdown and Workbench
        ↓
9. Sub-Skill JSON Manifest Generation
        ↓
10. Temporal Build Orchestration
        ↓
11. Hybrid Code Generation and Correction
        ↓
12. AST and Static Validation Gates
        ↓
13. Test Data, Promptfoo and CubeSandbox Execution
        ↓
14. Certification, Versioning, Freeze and Export
        ↓
15. End-to-End Testing, Security and Performance
        ↓
16. Environments, Operations, UAT and Go-Live
        ↓
Go-Live: first Workflow certified, frozen and exported in production
```

These work streams run in parallel once their foundations exist (Section 24):

```text
Contracts (Phase 0)
     ├── Dev B: ingestion → knowledge → SOW → approval → ISATVON
     ├── Dev C: Temporal skeleton → manifest → codegen → gates → test/sandbox → certification
     │          (driven by fixture manifests until real extraction lands)
     └── Dev D: Admin portal → each FDE screen one step behind its API → E2E

Platform (Phase 1, Dev A)
     ├── Keycloak / tenancy / RBAC
     ├── Runtime image + CubeSandbox host + Netbird (from week 4)
     ├── Observability (OTEL, Langfuse, PostHog)
     └── Environments, backup/DR
```

Phase 10 (Temporal) is listed after Phase 9 because its build stages consume manifests. Its skeleton is built from week 2 against stubs (SBAM-012/040), so it never blocks Phases 11–13.

---

# 28. Final Release Gate

SBAM should **not** be considered production-ready merely because:

- documents upload and a SOW appears;
- the grid shows Macro and Sub-Skills;
- code is generated and `ast.parse()` succeeds;
- a tool ran once in a local Docker container;
- Promptfoo passed on one happy-path case;
- a manifest file exists.

Production release requires:

```text
Frozen contracts (ISATVON v1, manifests v1)
     +
Phases 0–16 complete
     +
Security (tenant isolation, ClamAV, injection defences, immutability, image scans)
     +
Testing (E01–E20 green ×5, LLM golden sets, Temporal replay)
     +
Performance (targets below)
     +
Reproducibility (every frozen manifest re-verifies)
     +
Observability (OTEL + Langfuse + PostHog, in-app alerts)
     +
Backup/Recovery (restore drill passed)
     +
UAT (Admin and FDE sign-off)
     =
Production Ready
```

## Release acceptance targets

Confirmed by stakeholders (QA R1, R2):

- **Concurrency:** ≥ 10 concurrent users (MVP); ≥ 4 parallel Sub-Skill builds without degradation.
- **API p95:** ≤ 500 ms for interactive endpoints; ISATVON grid load ≤ 2 s for 500 Sub-Skills.
- **SOW draft:** ≤ 5 min after the knowledge version is READY (20 files × 15 MB maximum).
- **Validation gates:** ≤ 5 min per Sub-Skill (AST + static + security).
- **Sandbox + Promptfoo:** ≤ 10 min per Sub-Skill.
- **Availability target:** ≥ 99.5% during business hours, excluding planned maintenance.
- **Integrity:** 100% of frozen manifests re-verify their hash; 0 successful mutations in tamper tests.
- **Security:** 0 unresolved critical vulnerabilities; 0 cross-tenant access in tests; 100% of uploads ClamAV-scanned.
- **Recovery:** Restore tested successfully; RPO ≤ 24 h; RTO ≤ 8 h.
- **Testing:** 100% of E2E scenarios passing; ≥ 3 passing Promptfoo cases per certified Sub-Skill.
- **Operations:** Monitoring, in-app alerting, rollback and runbooks available.

---

# 29. Risks and Mitigations

| #   | Risk                                                            | Impact   | Prob.              | Mitigation                                                                                                    | Detection                         | Fallback                                                 |
| --- | --------------------------------------------------------------- | -------- | ------------------ | ------------------------------------------------------------------------------------------------------------- | --------------------------------- | -------------------------------------------------------- |
| R01 | LLM hallucination in SOW/ISATVON                                | High     | High               | Mandatory citations; FDE approval; dual context; golden sets                                                  | Citation coverage; FDE edit rate  | Regenerate section/macro with hint                       |
| R02 | LLM-written code is wrong but passes the gates                  | High     | Medium             | Manifest conformance, MyPy strict, ≥3 scenario tests incl. edge/negative, workflow smoke run                 | Post-certification defect reports | Add tests; re-certify as new patch version               |
| R03 | LLM correction edits Python and drifts from the manifest        | Medium   | Medium             | Protected skeleton regions; AST skeleton-integrity check; corrections re-run all gates                        | Skeleton diff failures            | Back to FDE after 2 attempts                             |
| R04 | OKF Bundle exceeds model context                                | Medium   | High (20 × 15 MB) | Ranked selection per SOW section from the bundle; large-context model alias; provenance kept                  | Token counts in Langfuse          | Section-wise generation (still one action for the FDE)   |
| R05 | Cognee default stores and isolation at scale                    | Medium   | Medium             | Dataset per project, access-control mode verified in spike; volume backups                                    | Isolation tests                   | Switch to Postgres-backed stores (Cognee supports it)    |
| R06 | CubeSandbox maturity / KVM host availability                    | High     | Medium             | Week-1 spike; dedicated Linux host; adapter abstraction; local-docker fallback (non-certifying)               | Spike, infra error rate           | Alternative MicroVM sandbox behind the adapter (ADR)     |
| R07 | Sandbox network access exfiltrates data                         | High     | Low                | Egress allowlist to mocks only; logging; synthetic data only                                                  | Egress logs                       | Disable egress per project                               |
| R08 | LLM-generated test data is unrealistic                          | Medium   | Medium             | Scenarios derived from I/V rules; FDE can edit and add cases; minimum 3 incl. edge and negative               | Low assertion diversity metric    | FDE-curated cases                                        |
| R09 | Masters / ISATVON / SOW template inputs arrive late             | High     | Medium             | Phase 0 blocker; build engines generically; fixtures                                                          | Phase 0 exit review               | Placeholder schemas, then migrate                        |
| R10 | OpenRouter instability or model changes                         | Medium   | Medium             | Bifrost failover across OpenRouter models; aliases; golden-set regression on model change                     | Error rates, quality drift        | Switch alias mapping                                     |
| R11 | Multi-tenant leakage                                            | Critical | Low                | Tenant-scoped repositories, Keycloak tenant claim, per-project Cognee datasets, path prefixes                 | IDOR suite                        | Incident procedure                                       |
| R12 | Next.js + Refine + AG Grid integration friction                 | Low      | Medium             | Client components for grid/editor; early shell spike                                                          | UI build issues                   | Plain Refine core hooks                                  |
| R13 | Local-disk storage limits growth before AWS                     | Medium   | Medium             | S3-compatible interface from day one; disk usage alerts                                                       | Disk metrics                      | Move to S3 early                                         |
| R14 | Aggressive timeline with 4 developers                           | High     | Medium             | Parallel streams, fixture-driven codegen, weekly integration points                                           | Burn-down per IP                  | Cut P1 items (explorer polish, export formats) before P0 |
| R15 | SBAM loop harness semantics differ from SRIM's BuLLM DMR loop   | High     | Medium             | Publish`loop_spec.json` semantics; SRIM parity review (O-17); harness tests on the ISA14 sample             | Parity test once SRIM exists      | Adjust harness; re-certify as patch                      |
| R16 | LLM writes weak gate expressions (`success_gate` always true) | High     | Medium             | Expression grammar + identifier resolution; ESCALATION scenario must actually escalate; NO_OP/RETRY scenarios | Scenario failures                 | Back to FDE                                              |
| R17 | Generated`schema.sql` conflicts across Sub-Skills             | Medium   | Medium             | Workflow-level merge + single validation; uploaded`.sql` as base when available                             | SCHEMA_SQL gate                   | FDE edits or uploads schema                              |

---

# 30. Open Items and Remaining Decisions

| ID   | Item                                                                                                               | Status                                                                                                          | Needed by    | Default if not answered                                                                                                                                    |
| ---- | ------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| O-01 | Masters schema + demo data                                                                                         | **Resolved:** README + five masters, frozen BAM 0.2 structure from MSC (TD §7.3.1)                       | —           | —                                                                                                                                                         |
| O-02 | ISATVON reference                                                                                                  | **Resolved:** ISATVON 1.4 (TD §10, §11)                                                                 | —           | —                                                                                                                                                         |
| O-03 | Code security scanner: ClamAV detects malware, not insecure Python. Add**Bandit** as SAST?                   | Recommendation                                                                                                  | Phase 12     | Bandit added (non-blocking warnings, blocking HIGH)                                                                                                        |
| O-04 | SOW template sections                                                                                              | **Resolved by design:** derived from ISATVON 1.4 (TD §10.6); confirm wording                             | Phase 6      | TD §10.6                                                                                                                                                  |
| O-05 | Keycloak tenancy: one realm with Organizations (recommended) vs realm per tenant                                   | Recommendation                                                                                                  | Phase 2      | One realm + Organizations                                                                                                                                  |
| O-06 | Jinja2 template ownership (I2 unanswered)                                                                          | Recommendation                                                                                                  | Phase 11     | Platform team (Dev C) in Git; no template UI in MVP                                                                                                        |
| O-07 | Major vs minor change definition                                                                                   | Recommendation                                                                                                  | Phase 14     | Major = new approved SOW version; patch = Sub-Skill rebuild under the same SOW                                                                             |
| O-08 | Workflow-level validation content                                                                                  | Recommendation                                                                                                  | Phase 14     | DAG completeness + interface compatibility + smoke run of the chain                                                                                        |
| O-09 | Log/trace backend behind OTEL                                                                                      | Recommendation                                                                                                  | Phase 16     | Grafana LGTM (Loki, Tempo, Prometheus, Grafana) in Docker                                                                                                  |
| O-10 | PostHog hosting                                                                                                    | Recommendation                                                                                                  | Phase 16     | Self-hosted on the dev/staging VM; optional local profile                                                                                                  |
| O-11 | Super-admin (platform) role for creating tenants                                                                   | Recommendation                                                                                                  | Phase 2      | `super-admin` role in Keycloak, Admin portal tenant screen                                                                                               |
| O-12 | Max files: per project vs per Workflow                                                                             | Recommendation                                                                                                  | Phase 4      | 20 active files per project (configurable)                                                                                                                 |
| O-13 | Who may restore deleted documents                                                                                  | Recommendation                                                                                                  | Phase 4      | FDE of the project and Admin                                                                                                                               |
| O-14 | Container registry                                                                                                 | Recommendation                                                                                                  | Phase 1      | GHCR (fits GitHub Actions)                                                                                                                                 |
| O-15 | Allowed`skill_type` values besides `Deterministic_Loopy_MCP`                                                   | Question                                                                                                        | Phase 9      | Only`Deterministic_Loopy_MCP`                                                                                                                            |
| O-16 | Foundational`schema.sql`: generated from V.tables, or always supplied by the FDE                                 | Recommendation                                                                                                  | Phase 9      | Uploaded`.sql` used as base if present; otherwise generated and validated                                                                                |
| O-17 | Parity of SBAM loop harness with SRIM BuLLM DMR loop semantics                                                     | Question for SRIM owners                                                                                        | Phase 13     | `loop_spec.json` semantics in TD §15.2                                                                                                                  |
| O-18 | Organization columns 7–11                                                                                         | **Resolved:** MSC has all 14 columns (Customers, Offerings, Competencies, Data Sources, Systems/Tools …) | —           | —                                                                                                                                                         |
| O-19 | `srim_runtime_definition` defaults (Dify DMR, MCP Jungle Gateway, SRIM UI Canvas): fixed per tenant or per skill | Recommendation                                                                                                  | Phase 9      | Tenant-level defaults from the ISA14 sample; not LLM-generated                                                                                             |
| O-20 | `max_iterations` upper limit                                                                                     | Recommendation                                                                                                  | Phase 0      | 1–10                                                                                                                                                      |
| O-21 | Intent classifier for master documents: MSC README proposes Jev / Laya ("accuracy untested")                       | Question                                                                                                        | Phase 3 (P1) | MVP routes by Filetype default route + Admin confirmation; an LLM classifier via Bifrost with a confidence threshold is optional; Jev/Laya evaluated later |
| O-22 | Status inside Misc cells (no Status column in BAM 0.2)                                                             | Recommendation                                                                                                  | Phase 3      | Parse`Status: <value>`; missing = PROPOSED; UI writes back into Misc                                                                                     |
| O-23 | Who may change the frozen Filetype table                                                                           | Recommendation                                                                                                  | Phase 3      | Only super-admin, through a new workbook version with owner sign-off                                                                                       |
| O-24 | BAM 0.2 sample rows (Unit/Rule/Agent/Skill#1) contain mock values (e.g., Rocketreach, Raytheon)                    | Recommendation                                                                                                  | Phase 3      | Imported as`ILLUSTRATIVE`; excluded from authoritative Cognee context                                                                                    |

---

# 31. Traceability Rule and Matrix

## 31.1 Rule

Every task must be traceable to at least one of: a confirmed decision (C-xx, Section 3), an architecture requirement (AR-xx below), a design section in TD, a test scenario (TD §19) or an operational target (Section 28).

Recommended task format:

```text
TASK-SBAM-XXX
Requirement: AR-xx / C-xx (source: PD §n, ARCH-n, QA Xn)
Design: TD §n.n
Implementation: <module / file>
Tests: E0x, unit/API/Temporal test ids
Status: <Not Started | In Progress | Review | Done>
```

This keeps the plan, the technical design, the architecture documents and the tests synchronized.

## 31.2 Matrix

| AR    | Requirement (source)                                                                       | Component       | API (TD §8) | Database (TD §7)                   | UI (TD §9)            | Temporal (TD §13)    | Test (TD §19) | Acceptance                                       |
| ----- | ------------------------------------------------------------------------------------------ | --------------- | ------------ | ----------------------------------- | ---------------------- | --------------------- | -------------- | ------------------------------------------------ |
| AR-01 | Two logins: Admin and FDE (QA A1, B1)                                                      | Identity        | 8.2          | `users`, `project_members`      | Login, portals         | —                    | E01            | Portal separation enforced                       |
| AR-02 | Multi-tenant isolation (QA B7)                                                             | Tenancy         | all          | `tenant_id` everywhere            | —                     | search attrs          | E02            | No cross-tenant access                           |
| AR-03 | Admin projects + 5 masters as Cognee context (QA C1–C4, ARCH-3)                           | Masters         | 8.3          | `projects`, `master_*`          | Admin portal           | knowledge WF          | E03            | Snapshot used in knowledge build                 |
| AR-04 | SBAM generates the Workflow from the approved SOW; FDE drives the project pipeline (owner correction) | Project pipeline + ISATVON | 8.4, 8.8 | `projects`, `workflows` | Project overview | `ProjectPipeline` | E04 | No API lets an FDE create a Workflow; Workflow exists only after SOW approval |
| AR-05 | Uppy upload, limits, ClamAV (QA D1–D4)                                                    | Ingestion       | 8.5          | `documents`                       | Upload                 | `DocumentIngestion` | E05, E06       | Unsafe files rejected                            |
| AR-06 | Basic OCR via LLM (QA D5)                                                                  | Ingestion       | 8.5          | `document_versions`               | Processing             | ingest                | E07            | Scanned pages extracted                          |
| AR-07 | Soft delete, 90-day recovery (QA D6)                                                       | Ingestion       | 8.5          | `deleted_at`                      | Recycle bin            | purge schedule        | E08            | Restore/purge works                              |
| AR-08 | Cognee graph per project, read-only explorer (QA E1, E3, E4)                               | Knowledge       | 8.6          | `knowledge_versions`              | Explorer               | knowledge             | E09            | Isolated, explorable                             |
| AR-09 | Information-preserving OKF Bundle (QA E2, PD §1b)                                         | Knowledge       | 8.6          | `artifacts`                       | Coverage               | knowledge             | E09            | 100% coverage                                    |
| AR-10 | One-shot cited SOW via Bifrost (QA F2, F3, PD §1c–d)                                     | SOW             | 8.7          | `sow_versions`, `llm_calls`     | SOW                    | SOW stage             | E10            | Cited valid SOW                                  |
| AR-11 | TipTap editing + export PDF/DOCX/MD (QA F2, F4)                                            | SOW             | 8.7          | `artifacts`                       | SOW                    | exports               | E11            | Exports valid                                    |
| AR-12 | FDE approval gate (QA B2, PD §2a)                                                         | SOW             | 8.7          | `sow_versions`                    | Approve                | signal                | E12            | Gate enforced                                    |
| AR-13 | Scope change → only affected rebuilt (QA F5)                                              | SOW + ISATVON   | 8.7          | `sub_skills.state`                | Impact preview         | rebuild signal        | E13            | Only affected rebuilt                            |
| AR-14 | Macro (agent, MSnn) → Sub-Skill (skill, MSnn.m), ISATVON 1.4 per Sub-Skill (QA G2, ISA14) | ISATVON         | 8.8          | `macro_skills`, `sub_skills`    | Workbench              | ISATVON stage         | E14            | All ISA14 elements + 5 loop bounds               |
| AR-15 | Grid safeguards (PD §2)                                                                   | Frontend        | 8.8          | —                                  | Workbench              | —                    | E15            | No crash, Generate gated                         |
| AR-16 | Excel export only (QA G3)                                                                  | ISATVON         | 8.8          | `artifacts`                       | Export                 | exports               | E14            | xlsx matches                                     |
| AR-17 | Skill Manifest in ISA14 structure as the codegen and SRIM contract (flow, QA H2, ISA14)    | Manifest        | 8.9          | `sub_skill_manifests`             | Viewer                 | build                 | E16            | ISA14 shape + deterministic hash                 |
| AR-18 | Hybrid Jinja2 + LLM codegen (QA I1, C-22)                                                  | Codegen         | 8.11         | `code_attempts`                   | Code viewer            | build                 | E16            | Skeleton intact, bodies from LLM                 |
| AR-19 | AST validates code vs manifest (flow, PD §3)                                              | Gates           | 8.10         | `gate_results`                    | Build dashboard        | build                 | E17            | Non-conforming code rejected                     |
| AR-20 | Correction: ISATVON + Python, 2 attempts (QA I4, I5)                                       | Correction      | 8.10         | `code_attempts`                   | Build dashboard        | build                 | E18            | Stops at 2 → FDE                                |
| AR-21 | Ruff, Black, MyPy strict, ClamAV (QA J2, J3, ARCH-3)                                       | Gates           | 8.10         | `gate_results`                    | Build dashboard        | build                 | E17            | All pass required                                |
| AR-22 | Runtime registry, Postgres only, all in Docker (QA J1)                                     | Registry        | 8.15         | `runtime_registry`                | Admin registry         | build                 | E16            | Missing deps fail                                |
| AR-23 | LLM test data, ≥3 Promptfoo tests (QA K2, K3, K5)                                         | Testing         | 8.12         | `test_cases`, `test_runs`       | Test panel             | test                  | E19            | ≥3 pass                                         |
| AR-24 | CubeSandbox on Linux/KVM, network allowed (QA K1, K4, PD §4a)                             | Sandbox         | 8.12         | `sandbox_runs`                    | Build dashboard        | test                  | E19            | Certifying runs isolated                         |
| AR-25 | Auto-certification Sub → Macro → Workflow (QA B4, H1)                                    | Certification   | 8.14         | `certifications`                  | Certification          | certify               | E20            | Only full evidence                               |
| AR-26 | Reuse unchanged certification (QA L3)                                                      | Certification   | 8.14         | `certifications`                  | Versions               | certify               | E13            | Unchanged reused                                 |
| AR-27 | MAJOR.0.PATCH, freeze, no revocation (QA L1, L2, PD §4d)                                  | Versioning      | 8.14         | triggers                            | Versions               | freeze                | E20            | Immutable                                        |
| AR-28 | Export package for SRIM; API future (QA M, H2)                                             | Export          | 8.14         | `artifacts`                       | Export                 | exports               | E20            | Checksums verify                                 |
| AR-29 | N = notify then next step; in-app alerts only (QA G4, Q2)                                  | Notifications   | 8.13         | `notifications`                   | Bell                   | notify activity       | E04–E20       | Every step notifies                              |
| AR-30 | Keycloak (QA B6)                                                                           | Identity        | 8.2          | —                                  | Login                  | —                    | E01            | OIDC working                                     |
| AR-31 | Next.js frontend (QA N1)                                                                   | Frontend        | —           | —                                  | all                    | —                    | E01–E20       | —                                               |
| AR-32 | Temporal self-hosted; Compose on VMs; 4 environments; GitHub Actions (QA N3–N8)           | Platform        | —           | —                                  | —                     | all                   | Deploy drill   | Repeatable deploys                               |
| AR-33 | Local disk storage behind S3 interface (QA N5)                                             | Storage         | —           | `artifacts`                       | —                     | —                    | Storage tests  | Swap to S3 = config                              |
| AR-34 | OpenRouter via Bifrost (QA O1)                                                             | LLM             | —           | `llm_calls`                       | —                     | all LLM               | Provider tests | All calls via Bifrost                            |
| AR-35 | OTEL + Langfuse + PostHog (QA Q1)                                                          | Observability   | —           | —                                  | —                     | interceptors          | Alert drill    | Correlated traces                                |
| AR-36 | Normal audit table (QA P2)                                                                 | Audit           | 8.16         | `audit_log`                       | Activity               | —                    | E02            | All actions audited                              |
| AR-37 | Frozen BAM 0.2 masters workbook, import/export (MSC)                                       | Masters         | 8.3          | `master_*`                        | Masters screen         | —                    | E03            | Round-trip identical; structure changes rejected |
| AR-38 | 43 frozen filetypes drive ingestion; private table (MSC)                                   | Ingestion       | 8.5          | `master_entries`                  | Upload                 | ingest                | E05            | Only enabled types accepted; FDE cannot edit     |
| AR-43 | Master documents routed by Filetype default route (MSC README §9)                         | Masters         | 8.3          | `master_documents`                | Master documents panel | ingest                | E03            | Routed to the right master                       |
| AR-44 | RAG Reference → Cognee node type/parent; Organization as graph root (MSC)                 | Knowledge       | 8.6          | `master_entry_versions`           | Explorer               | knowledge             | E09            | Master nodes linked to the Organization root     |
| AR-39 | Action tool + independent Fixed Check tool, no manual loops (ISA14 T, A)                   | Codegen + AST   | 8.11         | `code_attempts`, `gate_results` | Code viewer            | build                 | E17            | Decoupling and no-loop checks pass               |
| AR-40 | 5 mandatory loop bounds enforced in tests (ISA14 V)                                        | Loop harness    | 8.12         | `sandbox_runs`, `test_runs`     | Build & Test           | test                  | E19            | PASS_FIRST + ESCALATION pass                     |
| AR-41 | Append-only state handoff (ISA14 O)                                                        | Codegen + tests | 8.12         | `test_runs`                       | Harness trace          | test                  | E19            | Handoff rows appended, no updates                |
| AR-42 | Foundational PostgreSQL schema (ISA14)                                                     | Schema SQL      | 8.9          | `workflow_schemas`                | Schema view            | build                 | E16            | Applies cleanly                                  |

---

# 32. Final Implementation Roadmap

| Order | Phase             | Deliverables                                                    | Dependencies | Tasks    | Testing gate               | Exit criteria                       |
| ----- | ----------------- | --------------------------------------------------------------- | ------------ | -------- | -------------------------- | ----------------------------------- |
| 1st   | 0 Baseline        | Contracts v1, state machines, inputs as fixtures, spike reports | —           | 001–004 | Design review              | No blocking unknowns                |
| 2nd   | 1 Foundation      | Monorepo, Compose stack, Dockerfiles, CI, skeletons             | 0            | 005–013 | CI green,`make up`       | Stack runs on all dev machines      |
| 3rd   | 2 Identity        | Keycloak, tenancy, RBAC, audit                                  | 1            | 014–015 | RBAC + IDOR                | No cross-tenant access              |
| 4th   | 3 Admin portal    | Projects, masters, users                                        | 2            | 016–018 | Admin E2E                  | Masters snapshot ready (IP-1)       |
| 5th   | 4 Ingestion       | Workflow, Uppy, ClamAV, parsing, OCR, recycle bin               | 3            | 019–023 | Type fixtures, EICAR       | Files PARSED safely                 |
| 6th   | 5 Knowledge       | Cognee, OKF Bundle, explorer                                    | 4            | 024–027 | Isolation, coverage        | READY knowledge                     |
| 7th   | 6 SOW             | One-shot cited SOW, TipTap, export                              | 5            | 028–031 | Golden set                 | Editable cited SOW                  |
| 8th   | 7 Approval        | Gate, scope change, impact analysis                             | 6            | 032–033 | Gate tests                 | Approved SOW only (IP-2)            |
| 9th   | 8 ISATVON         | Macro → Sub-Skill breakdown, grid, Excel                       | 7            | 034–037 | Golden example, grid tests | Valid Sub-Skills                    |
| 10th  | 9 Manifest        | Sub-Skill + Workflow manifests, dependency check                | 8            | 038–039 | Determinism                | Stable hashes                       |
| 11th  | 10 Temporal       | Pipeline + Sub-Skill build workflows, notifications             | 9            | 040–041 | WF env + replay            | All paths tested                    |
| 12th  | 11 Codegen        | Skeleton templates, LLM bodies, correction, code viewer         | 10           | 042–045 | Snapshots, fixtures        | Tools assembled (IP-3)              |
| 13th  | 12 Gates          | AST conformance, static/security gates, runtime registry        | 11           | 046–049 | Negative fixtures          | Only valid code proceeds            |
| 14th  | 13 Test & sandbox | Test data, Promptfoo, CubeSandbox host + adapter, mocks         | 12           | 050–054 | Meta-test, egress test     | ≥3 cases pass in MicroVM           |
| 15th  | 14 Certification  | 3-level certification, versions, freeze, export                 | 13           | 055–058 | Tamper tests               | Frozen exportable Workflow (IP-4)   |
| 16th  | 15 E2E & security | E01–E20, security, performance, LLM quality                    | 2–14        | 059–061 | Green ×5                  | Targets met                         |
| 17th  | 16 Ops & go-live  | Environments, observability, backup, UAT                        | 15           | 062–065 | Restore + UAT              | First Workflow frozen in production |
| Later | Future            | Approval integrations, ingestion channels, SRIM handover, AWS   | MVP          | 066–069 | Per feature                | Per feature DoD                     |

**What to build first, second, third:**

1. Collect the three inputs and freeze the contracts.
2. Bring up the Docker stack, CI, Keycloak and tenancy.
3. Let the Admin set up projects and masters.
4. Follow the data through the system: ingestion → knowledge → SOW → approval → ISATVON → manifests. Meanwhile, Dev C builds the Temporal backbone, code generation and gates against fixture manifests.
5. Connect the real manifests to code generation, gates, sandbox and certification.
6. Freeze and export the Workflow.
7. Prove everything end to end, then operate it.
