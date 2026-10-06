> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 9 — Sub-Skill JSON Manifest Generation

| Epic | Milestone | Stories |
|---|---|---|
| [#10](https://github.com/solutionsDigibull/BAM/issues/10) `E9` | M3 Breakdown + Build | 6 |


**Goal:** Convert each Sub-Skill's ISATVON 1.4 definition into its Skill Manifest (exact ISA14 structure + `sbam_build_record`), the single contract for code generation, validation and SRIM, and build the Workflow `schema.sql`.

**Owner:** Dev C · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 8
**Design references:** [TD §11](../design/11-manifest-design.md).

### Backend

- [ ] Manifest builder ([TD §11.1](../design/11-manifest-design.md)): `skill_metadata` (ids, type `Deterministic_Loopy_MCP`, reusable, version, status, owner, timestamps), `isatvon_definition` (ISA14 shape), `srim_runtime_definition` (tenant defaults), `contracts` (input from S.required_inputs, output from O.json_schema + `state_handoff_id`), `governance` (hash, lifecycle, versioning), `sbam_build_record`.
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

## Design pages to read

- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §10. ISATVON 1.4 contract (Sub-Skill level)](../design/10-isatvon-1-4-contract.md)
- [TD §11. Manifest design](../design/11-manifest-design.md)
- [TD §12. Code generation (hybrid Jinja2 + LLM)](../design/12-code-generation.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#101](https://github.com/solutionsDigibull/BAM/issues/101) | `E9.1` | Skill Manifest builder (ISA14 shape + sbam_build_record) | C Build | M | P0 | M3 | [#20](https://github.com/solutionsDigibull/BAM/issues/20), [#93](https://github.com/solutionsDigibull/BAM/issues/93) |
| [#102](https://github.com/solutionsDigibull/BAM/issues/102) | `E9.2` | loop_spec compiler + safe expression evaluator | C Build | M | P0 | M3 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#103](https://github.com/solutionsDigibull/BAM/issues/103) | `E9.3` | Library check vs runtime registry | C Build | S | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#104](https://github.com/solutionsDigibull/BAM/issues/104) | `E9.4` | Workflow schema.sql builder + validation | B Knowledge/LLM | M | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101) |
| [#105](https://github.com/solutionsDigibull/BAM/issues/105) | `E9.5` | Workflow manifest draft | C Build | S | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#104](https://github.com/solutionsDigibull/BAM/issues/104) |
| [#106](https://github.com/solutionsDigibull/BAM/issues/106) | `E9.6` | Manifest + schema.sql viewer (Monaco) | D Frontend | S | P1 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101) |

---
[← Phase 8](phase-08-isatvon-breakdown-and-workbench.md)   [Phases index](README.md)   [Phase 10 →](phase-10-temporal-build-orchestration.md)
