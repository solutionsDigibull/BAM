> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 15 — End-to-End Testing, Security and Performance

| Epic | Milestone | Stories |
|---|---|---|
| [#16](https://github.com/solutionsDigibull/BAM/issues/16) `E15` | M5 Harden + Go-live | 5 |


**Goal:** Prove the full SBAM journey works and fails safely, and meets the targets, in a production-like Docker environment.

**Owner:** All (Dev D leads E2E) · **Indicative effort:** 1.5 weeks (starts in parallel from Phase 8) · **Complexity:** L · **Depends on:** Phases 2–14
**Design references:** [TD §19](../design/19-testing-strategy.md).

### Functional

- [ ] Automate scenarios E01–E20 ([TD §19.2](../design/19-testing-strategy.md)) with Playwright on the staging Compose stack + CubeSandbox host.
- [ ] Rework paths: SOW edit/re-approval, scope change, correction loop, FDE fix after 2 failed corrections.

### Security

- [ ] Cross-tenant/project IDOR suite; Keycloak token abuse; prompt-injection documents; template/code-injection via ISATVON fields.
- [ ] Frozen-record tamper tests; ClamAV EICAR; sandbox egress tests; Trivy image scan with no CRITICAL findings.

### Performance

- [ ] 10 concurrent users; 4 parallel Sub-Skill builds; 20 × 15 MB ingestion; grid with 500 Sub-Skills.

### LLM quality

- [ ] Golden sets for OCR, SOW, ISATVON, codegen, testgen and correction; regression gate on prompt or model change.

### Exit Criteria

- [ ] All E2E scenarios pass in five consecutive CI runs on staging.
- [ ] No unresolved critical/high security findings.
- [ ] Release targets in Section 28 are met.

## Design pages to read

- [TD §16. Security and multi-tenancy](../design/16-security-and-multi-tenancy.md)
- [TD §19. Testing strategy](../design/19-testing-strategy.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#135](https://github.com/solutionsDigibull/BAM/issues/135) | `E15.1` | Playwright harness + scenarios E01–E10 | D Frontend | L | P0 | M2 | [#96](https://github.com/solutionsDigibull/BAM/issues/96), [#77](https://github.com/solutionsDigibull/BAM/issues/77) |
| [#136](https://github.com/solutionsDigibull/BAM/issues/136) | `E15.2` | Scenarios E11–E20 | D Frontend | L | P0 | M5 | [#134](https://github.com/solutionsDigibull/BAM/issues/134), [#135](https://github.com/solutionsDigibull/BAM/issues/135) |
| [#137](https://github.com/solutionsDigibull/BAM/issues/137) | `E15.3` | Security suite (IDOR, tokens, injection, tamper, egress, scans) | A Platform | M | P0 | M5 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#138](https://github.com/solutionsDigibull/BAM/issues/138) | `E15.4` | Performance tests | B Knowledge/LLM | M | P1 | M5 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#139](https://github.com/solutionsDigibull/BAM/issues/139) | `E15.5` | LLM golden sets + regression gate | B Knowledge/LLM | M | P0 | M4 | [#93](https://github.com/solutionsDigibull/BAM/issues/93), [#113](https://github.com/solutionsDigibull/BAM/issues/113) |

---
[← Phase 14](phase-14-certification-versioning-freeze-and-export.md)   [Phases index](README.md)   [Phase 16 →](phase-16-environments-operations-uat-and-go-live.md)
