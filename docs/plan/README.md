> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Implementation Plan (PLAN) — index


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



| PLAN § | Page |
|---|---|
| 1 | [Executive Summary and Implementation Objectives](01-objectives-and-scope.md) |
| 2 | [Scope](01-objectives-and-scope.md) |
| 3 | [Confirmed Decisions](02-confirmed-decisions.md) |
| 4 | [Architecture Baseline](03-architecture-and-principles.md) |
| 5 | [Engineering Principles](03-architecture-and-principles.md) |
| 6 | [Phase 0 — Architecture, Contracts and Inputs Baseline](../phases/phase-00-architecture-contracts-and-inputs-baseline.md) |
| 7 | [Phase 1 — Repository Foundation and Containerized Dev Stack](../phases/phase-01-repository-foundation-and-containerized-dev-stack.md) |
| 8 | [Phase 2 — Identity, Multi-Tenancy and RBAC](../phases/phase-02-identity-multi-tenancy-and-rbac.md) |
| 9 | [Phase 3 — Admin Portal: Projects and Masters](../phases/phase-03-admin-portal-projects-and-masters.md) |
| 10 | [Phase 4 — Project Workspace and Document Ingestion](../phases/phase-04-project-workspace-and-document-ingestion.md) |
| 11 | [Phase 5 — Knowledge Construction (Cognee, OKF Bundle, Graph Explorer)](../phases/phase-05-knowledge-construction.md) |
| 12 | [Phase 6 — SOW Generation, Editing and Export](../phases/phase-06-sow-generation-editing-and-export.md) |
| 13 | [Phase 7 — SOW Approval (HITL) and Scope-Change Versioning](../phases/phase-07-sow-approval-and-scope-change-versioning.md) |
| 14 | [Phase 8 — ISATVON Breakdown and Workbench](../phases/phase-08-isatvon-breakdown-and-workbench.md) |
| 15 | [Phase 9 — Sub-Skill JSON Manifest Generation](../phases/phase-09-sub-skill-json-manifest-generation.md) |
| 16 | [Phase 10 — Temporal Build Orchestration](../phases/phase-10-temporal-build-orchestration.md) |
| 17 | [Phase 11 — Hybrid Code Generation (Jinja2 Skeleton + LLM Body) and Correction](../phases/phase-11-hybrid-code-generation-and-correction.md) |
| 18 | [Phase 12 — AST and Static Validation Gates](../phases/phase-12-ast-and-static-validation-gates.md) |
| 19 | [Phase 13 — Test Data, Promptfoo and CubeSandbox Execution](../phases/phase-13-test-data-promptfoo-and-cubesandbox-execution.md) |
| 20 | [Phase 14 — Certification, Versioning, Freeze and Export](../phases/phase-14-certification-versioning-freeze-and-export.md) |
| 21 | [Phase 15 — End-to-End Testing, Security and Performance](../phases/phase-15-end-to-end-testing-security-and.md) |
| 22 | [Phase 16 — Environments, Operations, UAT and Go-Live](../phases/phase-16-environments-operations-uat-and-go-live.md) |
| 23 | [Task Breakdown](04-task-breakdown.md) |
| 24 | [Team Allocation and Parallel Development Plan](05-team-and-parallel-plan.md) |
| 25 | [MVP Definition](06-mvp-dod-delivery-release.md) |
| 26 | [Definition of Done](06-mvp-dod-delivery-release.md) |
| 27 | [Recommended Delivery Order](06-mvp-dod-delivery-release.md) |
| 28 | [Final Release Gate](06-mvp-dod-delivery-release.md) |
| 29 | [Risks and Mitigations](07-risks-and-open-items.md) |
| 30 | [Open Items and Remaining Decisions](07-risks-and-open-items.md) |
| 31 | [Traceability Rule and Matrix](08-traceability-and-roadmap.md) |
| 32 | [Final Implementation Roadmap](08-traceability-and-roadmap.md) |
