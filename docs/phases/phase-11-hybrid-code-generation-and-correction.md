> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 11 — Hybrid Code Generation (Jinja2 Skeleton + LLM Body) and Correction

| Epic | Milestone | Stories |
|---|---|---|
| [#12](https://github.com/solutionsDigibull/BAM/issues/12) `E11` | M3 Breakdown + Build | 5 |


**Goal:** Generate each Sub-Skill's FastMCP tool from its manifest: a deterministic Jinja2 skeleton plus LLM-written logic, with up to two automatic corrections.

**Owner:** Dev C · **Indicative effort:** 2 weeks · **Complexity:** XL · **Depends on:** Phase 10
**Design references:** [TD §12](../design/12-code-generation.md).

### Backend

- [ ] Skeleton templates in Git (`templates/fastmcp-loopy/<version>/`, [TD §12.3](../design/12-code-generation.md)): server registering the **Action tool** and the independent **Fixed Check tool**, Pydantic models from `contracts`, fresh-state reader, append-only state-handoff writer, imports from resolved libraries, logging, and `# >>> LLM_BODY:<name>` protected slots.
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

## Design pages to read

- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §12. Code generation (hybrid Jinja2 + LLM)](../design/12-code-generation.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#112](https://github.com/solutionsDigibull/BAM/issues/112) | `E11.1` | fastmcp-loopy skeleton templates + golden snapshots | C Build | L | P0 | M2 | [#29](https://github.com/solutionsDigibull/BAM/issues/29), [#20](https://github.com/solutionsDigibull/BAM/issues/20) |
| [#113](https://github.com/solutionsDigibull/BAM/issues/113) | `E11.2` | LLM body generation (action, check, state queries) | C Build | M | P0 | M3 | [#112](https://github.com/solutionsDigibull/BAM/issues/112), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#114](https://github.com/solutionsDigibull/BAM/issues/114) | `E11.3` | Assembly + Black + bundle storage | C Build | S | P0 | M3 | [#113](https://github.com/solutionsDigibull/BAM/issues/113) |
| [#115](https://github.com/solutionsDigibull/BAM/issues/115) | `E11.4` | Correction activity (ISATVON patch + Python edit, max 2) | C Build | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114), [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#116](https://github.com/solutionsDigibull/BAM/issues/116) | `E11.5` | Code viewer (attempts, diffs, findings markers) | D Frontend | S | P1 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114) |

---
[← Phase 10](phase-10-temporal-build-orchestration.md)   [Phases index](README.md)   [Phase 12 →](phase-12-ast-and-static-validation-gates.md)
