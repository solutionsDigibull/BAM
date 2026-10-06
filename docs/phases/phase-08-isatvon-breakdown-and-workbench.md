> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 8 — ISATVON Breakdown and Workbench

| Epic | Milestone | Stories |
|---|---|---|
| [#9](https://github.com/solutionsDigibull/BAM/issues/9) `E8` | M3 Breakdown + Build | 8 |


**Goal:** Break the approved SOW into Macro Skills (agents) and Sub-Skills (skills), with a complete ISATVON JSON per Sub-Skill, and give the FDE a safe AG Grid workbench to review and edit it.

**Owner:** Dev B (extraction) + Dev D (grid) · **Indicative effort:** 2 weeks · **Complexity:** XL · **Depends on:** Phase 7
**Design references:** [TD §10](../design/10-isatvon-1-4-contract.md), [TD §8.8](../design/08-api-design.md), [TD §9.3](../design/09-frontend-design.md).

### Backend

- [ ] On SOW approval, SBAM creates the **Workflow** record for that approved SOW version (one Workflow per approved SOW version), then runs the breakdown into it.
- [ ] Two-pass extraction via `sbam-isatvon` (Pydantic AI): pass 1 = Macro Skills (`MSnn_<Name>`, optional Agents-master ref) from the approved SOW + OKF context; pass 2 = Sub-Skills (`MSnn.m_<Name>`) per Macro with the full ISATVON 1.4 definition.
- [ ] Validation per Sub-Skill ([TD §10.4](../design/10-isatvon-1-4-contract.md)): all elements and sub-fields, ID patterns, I/A consistency, action tool ≠ fixed check tool, the 5 loop bounds and gate expressions, contracts, state-handoff table in V.tables, N progress/escalation messages, libraries in registry, SOW citations.
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

## Design pages to read

- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §10. ISATVON 1.4 contract (Sub-Skill level)](../design/10-isatvon-1-4-contract.md)
- [TD §14. State machines](../design/14-state-machines.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#91](https://github.com/solutionsDigibull/BAM/issues/91) | `E8.1` | SBAM creates the Workflow on SOW approval | C Build | S | P0 | M3 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#92](https://github.com/solutionsDigibull/BAM/issues/92) | `E8.2` | Macro Skill extraction (pass 1) | B Knowledge/LLM | M | P0 | M3 | [#91](https://github.com/solutionsDigibull/BAM/issues/91), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#93](https://github.com/solutionsDigibull/BAM/issues/93) | `E8.3` | Sub-Skill ISATVON 1.4 extraction (pass 2) + validation | B Knowledge/LLM | L | P0 | M3 | [#92](https://github.com/solutionsDigibull/BAM/issues/92), [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#94](https://github.com/solutionsDigibull/BAM/issues/94) | `E8.4` | ISATVON edit/delete/reorder/regenerate APIs + field audit | B Knowledge/LLM | M | P0 | M3 | [#93](https://github.com/solutionsDigibull/BAM/issues/93) |
| [#97](https://github.com/solutionsDigibull/BAM/issues/97) | `E8.5` | ISATVON Excel export (openpyxl) | B Knowledge/LLM | S | P1 | M3 | [#94](https://github.com/solutionsDigibull/BAM/issues/94) |
| [#98](https://github.com/solutionsDigibull/BAM/issues/98) | `E8.6` | Grid metadata schema endpoint | B Knowledge/LLM | S | P0 | M3 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#99](https://github.com/solutionsDigibull/BAM/issues/99) | `E8.7` | AG Grid workbench (tree, ISATVON column groups, editors, safeguards) | D Frontend | L | P0 | M3 | [#98](https://github.com/solutionsDigibull/BAM/issues/98), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |
| [#100](https://github.com/solutionsDigibull/BAM/issues/100) | `E8.8` | Gate-expression editor, Sub-Skill drawer, I/O dependency view | D Frontend | M | P0 | M3 | [#99](https://github.com/solutionsDigibull/BAM/issues/99) |

---
[← Phase 7](phase-07-sow-approval-and-scope-change-versioning.md)   [Phases index](README.md)   [Phase 9 →](phase-09-sub-skill-json-manifest-generation.md)
