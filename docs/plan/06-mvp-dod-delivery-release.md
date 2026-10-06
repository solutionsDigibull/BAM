> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# PLAN — MVP, Definition of Done, Delivery Order and Release Gate

## PLAN §25. MVP Definition


### 25.1 MVP must deliver end to end

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

### 25.2 After MVP

Email/other approval integrations; API/webhook/cron/email/watchdog ingestion; advanced OCR; AWS hosting and S3 (+ Object Lock); SRIM handover API and acknowledgement; manifest signing; compliance controls; scaling beyond 10 concurrent users.

## PLAN §26. Definition of Done


### 26.1 A Sub-Skill is certified only when

- [ ] Its Workflow's SOW version is approved by an FDE (hash-bound approval).
- [ ] Its ISATVON 1.4 definition is valid: all elements, the 5 loop bounds, decoupled action/check tools, state handoff and SOW citations.
- [ ] Its Skill Manifest matches the ISA14 structure and every library resolves in the runtime registry.
- [ ] Code (action tool, fixed check tool, handoff writer) was generated from a pinned skeleton template plus LLM bodies, within ≤2 corrections.
- [ ] AST validation passed: syntax, skeleton integrity, conformance, decoupling, no manual loops, handoff, import policy, SQL safety.
- [ ] Ruff, Black, MyPy strict, ClamAV (and Bandit) passed on the same code hash.
- [ ] At least 3 loop scenarios (including PASS_FIRST and ESCALATION) passed through the harness in a certifying CubeSandbox run on a hashed synthetic dataset, with handoff rows appended.
- [ ] A certification record exists, and the manifest has `status` = `APPROVED`, a version and `deterministic_hash`.

### 26.2 A Macro Skill is certified only when

- [ ] Every one of its Sub-Skills is certified on current hashes.
- [ ] Its internal dependency graph is complete and acyclic.

### 26.3 A Workflow is certified and frozen only when

- [ ] Every Macro Skill is certified.
- [ ] Workflow validation passed (DAG complete, no dangling inputs, `schema.sql` complete, workflow-level smoke run).
- [ ] The Workflow manifest is assembled, versioned (MAJOR.0.PATCH), hashed and frozen.
- [ ] All referenced artifacts are immutable and hash-verified.
- [ ] The export package is generated and its checksums verify.

### 26.4 A feature is Done only when

- [ ] It references the related decision/requirement IDs (C-xx / AR-xx).
- [ ] The API is in OpenAPI and the TS types are regenerated.
- [ ] Tenant/project authorization is implemented and covered by RBAC/IDOR tests.
- [ ] Server-side Pydantic validation is implemented.
- [ ] The Alembic migration is added and reversible.
- [ ] State changes go through the state machine service and write the audit table.
- [ ] Errors map to the failure matrix ([TD §18](../design/18-error-handling-matrix.md)) with user-visible messages.
- [ ] OTEL traces/logs carry tenant, project, workflow and correlation IDs; LLM calls are traced in Langfuse.
- [ ] Unit + integration tests exist; Temporal tests for workflow changes; E2E for user journeys.
- [ ] Loading, empty, error and forbidden UI states are implemented.
- [ ] The Docker image builds and scans clean, and the Compose stack starts.
- [ ] Docs and ADRs are updated, CI is green and the staging smoke test passes.

## PLAN §27. Recommended Delivery Order


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

## PLAN §28. Final Release Gate


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

### Release acceptance targets

Confirmed by stakeholders (QA R1, R2):

- **Concurrency:** ≥ 10 concurrent users (MVP); ≥ 4 parallel Sub-Skill builds without degradation.
- **API p95:** ≤ 500 ms for interactive endpoints; ISATVON grid load ≤ 2 s for 500 Sub-Skills.
- **SOW draft:** ≤ 5 min after the knowledge version is READY (20 files × 15 MB maximum).
- **Validation gates:** ≤ 5 min per Sub-Skill (AST + static + security).
- **Sandbox + Promptfoo:** ≤ 10 min per Sub-Skill.
- **Availability target:** ≥ 99.5% during business hours, excluding planned maintenance.
- **Integrity:** 100% of frozen manifests re-verify their hash; 0 successful mutations in tamper tests.
- **Security:** 0 unresolved critical **or high** vulnerabilities at release (CI blocks PRs on CRITICAL; HIGH findings must be fixed or formally accepted before release); 0 cross-tenant access in tests; 100% of uploads ClamAV-scanned.
- **Recovery:** Restore tested successfully; RPO ≤ 24 h; RTO ≤ 8 h.
- **Testing:** 100% of E2E scenarios passing; ≥ 3 passing Promptfoo cases per certified Sub-Skill.
- **Operations:** Monitoring, in-app alerting, rollback and runbooks available.
