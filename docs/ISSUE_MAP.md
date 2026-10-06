# SBAM Issue Map

All work lives in [solutionsDigibull/BAM issues](https://github.com/solutionsDigibull/BAM/issues): **17 epics, 128 stories, 5 future items**. This page is generated from `issues_spec.py`. Issue bodies are the source of truth for scope and acceptance criteria.

Legend: **Stream** A platform/DevOps/security · B knowledge/LLM · C build pipeline · D frontend — **Size** S ≤1 d · M 2–3 d · L 4–5 d.

## Milestones

| Milestone | Goal / demo | Stories |
|---|---|---|
| M0 Foundation | Repo, Docker stack, CI, contracts, identity baseline, spikes | 36 |
| M1 Admin + Ingestion | Admin masters, project workspace, upload → ClamAV → parsed (IP-1) | 23 |
| M2 Knowledge + SOW | Cognee + OKF, one-shot SOW, FDE approval (IP-2) | 17 |
| M3 Breakdown + Build | ISATVON 1.4 breakdown, manifests, codegen, gates (IP-3) | 31 |
| M4 Test + Certify | Loop harness in CubeSandbox, Promptfoo, certification, freeze, export (IP-4) | 15 |
| M5 Harden + Go-live | E2E, security, performance, environments, UAT, go-live | 6 |
| Future | Post-MVP scope | 5 |

## Start here (no dependencies)

- [#18](https://github.com/solutionsDigibull/BAM/issues/18) `E0.1` ADRs for confirmed decisions and open-item defaults — A Platform, S
- [#19](https://github.com/solutionsDigibull/BAM/issues/19) `E0.2` ISATVON 1.4 Pydantic contract + gate-expression grammar — C Build, L
- [#21](https://github.com/solutionsDigibull/BAM/issues/21) `E0.4` Admin masters schemas (BAM 0.2) + RAG Reference + stable-ID/status rules — B Knowledge/LLM, M
- [#23](https://github.com/solutionsDigibull/BAM/issues/23) `E0.6` State-machine definitions as code (project pipeline, document, SOW, Workflow, macro, sub-skill) — A Platform, S
- [#24](https://github.com/solutionsDigibull/BAM/issues/24) `E0.7` API conventions + problem+json error schema — A Platform, S
- [#25](https://github.com/solutionsDigibull/BAM/issues/25) `E0.8` Golden fixture pack — B Knowledge/LLM, S
- [#26](https://github.com/solutionsDigibull/BAM/issues/26) `E0.9` Spike: Cognee default stores + OKF bundle — B Knowledge/LLM, M
- [#30](https://github.com/solutionsDigibull/BAM/issues/30) `E0.12` Spike: Promptfoo against a FastMCP tool — C Build, S
- [#31](https://github.com/solutionsDigibull/BAM/issues/31) `E0.13` Spike: Keycloak Organizations + Auth.js + FastAPI JWT — A Platform, S
- [#27](https://github.com/solutionsDigibull/BAM/issues/27) `E0.14` Prerequisites: GHCR, OpenRouter key, KVM host, dev VM, Netbird — A Platform, S
- [#32](https://github.com/solutionsDigibull/BAM/issues/32) `E1.1` Monorepo scaffold and tooling — A Platform, M

## Critical path (≈66 dev-days end to end)

> The Docker stack (#38 → #39) is the first platform milestone: every issue that needs a running service is blocked by #39
> ([TD §20](design/20-docker-and-environments.md)). GitHub `Blocked by` lines are authoritative.

These issues gate the final delivery. Keep them moving first:

[#32](https://github.com/solutionsDigibull/BAM/issues/32) `E1.1` → [#38](https://github.com/solutionsDigibull/BAM/issues/38) `E1.9` → [#39](https://github.com/solutionsDigibull/BAM/issues/39) `E1.10` → [#47](https://github.com/solutionsDigibull/BAM/issues/47) `E2.1` → [#48](https://github.com/solutionsDigibull/BAM/issues/48) `E2.2` → [#54](https://github.com/solutionsDigibull/BAM/issues/54) `E3.1` → [#55](https://github.com/solutionsDigibull/BAM/issues/55) `E3.2` → [#60](https://github.com/solutionsDigibull/BAM/issues/60) `E3.7` → [#70](https://github.com/solutionsDigibull/BAM/issues/70) `E4.4` → [#71](https://github.com/solutionsDigibull/BAM/issues/71) `E4.5` → [#80](https://github.com/solutionsDigibull/BAM/issues/80) `E5.2` → [#81](https://github.com/solutionsDigibull/BAM/issues/81) `E5.3` → [#86](https://github.com/solutionsDigibull/BAM/issues/86) `E6.2` → [#87](https://github.com/solutionsDigibull/BAM/issues/87) `E6.3` → [#90](https://github.com/solutionsDigibull/BAM/issues/90) `E7.1` → [#91](https://github.com/solutionsDigibull/BAM/issues/91) `E8.1` → [#92](https://github.com/solutionsDigibull/BAM/issues/92) `E8.2` → [#93](https://github.com/solutionsDigibull/BAM/issues/93) `E8.3` → [#101](https://github.com/solutionsDigibull/BAM/issues/101) `E9.1` → [#125](https://github.com/solutionsDigibull/BAM/issues/125) `E13.4` → [#127](https://github.com/solutionsDigibull/BAM/issues/127) `E13.6` → [#129](https://github.com/solutionsDigibull/BAM/issues/129) `E14.1` → [#130](https://github.com/solutionsDigibull/BAM/issues/130) `E14.2` → [#132](https://github.com/solutionsDigibull/BAM/issues/132) `E14.4` → [#133](https://github.com/solutionsDigibull/BAM/issues/133) `E14.5` → [#134](https://github.com/solutionsDigibull/BAM/issues/134) `E14.6` → [#136](https://github.com/solutionsDigibull/BAM/issues/136) `E15.2` → [#145](https://github.com/solutionsDigibull/BAM/issues/145) `E16.6`

## Phases and epics

Each phase page lists its stories with stream, size, priority and dependencies.

| Phase | Epic | Milestone | Stories | Page |
|---|---|---|---|---|
| 0 | [#1](https://github.com/solutionsDigibull/BAM/issues/1) Architecture, Contracts and Inputs Baseline | M0 Foundation | 14 | [phase-00](phases/phase-00-architecture-contracts-and-inputs-baseline.md) |
| 1 | [#2](https://github.com/solutionsDigibull/BAM/issues/2) Repository Foundation and Containerized Dev Stack | M0 Foundation | 15 | [phase-01](phases/phase-01-repository-foundation-and-containerized-dev-stack.md) |
| 2 | [#3](https://github.com/solutionsDigibull/BAM/issues/3) Identity, Multi-Tenancy and RBAC | M0 Foundation | 7 | [phase-02](phases/phase-02-identity-multi-tenancy-and-rbac.md) |
| 3 | [#4](https://github.com/solutionsDigibull/BAM/issues/4) Admin Portal: Projects and Masters (BAM 0.2) | M1 Admin + Ingestion | 12 | [phase-03](phases/phase-03-admin-portal-projects-and-masters.md) |
| 4 | [#5](https://github.com/solutionsDigibull/BAM/issues/5) Project Workspace and Document Ingestion | M1 Admin + Ingestion | 11 | [phase-04](phases/phase-04-project-workspace-and-document-ingestion.md) |
| 5 | [#6](https://github.com/solutionsDigibull/BAM/issues/6) Knowledge Construction (Cognee, OKF, Explorer) | M2 Knowledge + SOW | 6 | [phase-05](phases/phase-05-knowledge-construction.md) |
| 6 | [#7](https://github.com/solutionsDigibull/BAM/issues/7) SOW Generation, Editing and Export | M2 Knowledge + SOW | 5 | [phase-06](phases/phase-06-sow-generation-editing-and-export.md) |
| 7 | [#8](https://github.com/solutionsDigibull/BAM/issues/8) SOW Approval (HITL) and Scope-Change Versioning | M2 Knowledge + SOW | 3 | [phase-07](phases/phase-07-sow-approval-and-scope-change-versioning.md) |
| 8 | [#9](https://github.com/solutionsDigibull/BAM/issues/9) ISATVON 1.4 Breakdown and Workbench | M3 Breakdown + Build | 8 | [phase-08](phases/phase-08-isatvon-breakdown-and-workbench.md) |
| 9 | [#10](https://github.com/solutionsDigibull/BAM/issues/10) Skill Manifest and Workflow schema.sql | M3 Breakdown + Build | 6 | [phase-09](phases/phase-09-sub-skill-json-manifest-generation.md) |
| 10 | [#11](https://github.com/solutionsDigibull/BAM/issues/11) Temporal Orchestration (ProjectPipeline + SubSkillBuild) | M3 Breakdown + Build | 6 | [phase-10](phases/phase-10-temporal-build-orchestration.md) |
| 11 | [#12](https://github.com/solutionsDigibull/BAM/issues/12) Hybrid Code Generation and Correction | M3 Breakdown + Build | 5 | [phase-11](phases/phase-11-hybrid-code-generation-and-correction.md) |
| 12 | [#13](https://github.com/solutionsDigibull/BAM/issues/13) AST and Static Validation Gates | M3 Breakdown + Build | 6 | [phase-12](phases/phase-12-ast-and-static-validation-gates.md) |
| 13 | [#14](https://github.com/solutionsDigibull/BAM/issues/14) Test Data, Loop Harness, Promptfoo and CubeSandbox | M4 Test + Certify | 7 | [phase-13](phases/phase-13-test-data-promptfoo-and-cubesandbox-execution.md) |
| 14 | [#15](https://github.com/solutionsDigibull/BAM/issues/15) Certification, Versioning, Freeze and Export | M4 Test + Certify | 6 | [phase-14](phases/phase-14-certification-versioning-freeze-and-export.md) |
| 15 | [#16](https://github.com/solutionsDigibull/BAM/issues/16) End-to-End Testing, Security and Performance | M5 Harden + Go-live | 5 | [phase-15](phases/phase-15-end-to-end-testing-security-and.md) |
| 16 | [#17](https://github.com/solutionsDigibull/BAM/issues/17) Environments, Operations, UAT and Go-Live | M5 Harden + Go-live | 6 | [phase-16](phases/phase-16-environments-operations-uat-and-go-live.md) |

## Future (post-MVP)

- [#146](https://github.com/solutionsDigibull/BAM/issues/146) Email / external approval integration for SOW
- [#147](https://github.com/solutionsDigibull/BAM/issues/147) API / webhook / cron / email / FastMCP-watchdog ingestion channels
- [#148](https://github.com/solutionsDigibull/BAM/issues/148) SRIM handover API, acknowledgement and environment switch
- [#149](https://github.com/solutionsDigibull/BAM/issues/149) AWS hosting, S3 backend, Object Lock and manifest signing
- [#150](https://github.com/solutionsDigibull/BAM/issues/150) Intent classifier for master documents (Jev / Laya evaluation)
