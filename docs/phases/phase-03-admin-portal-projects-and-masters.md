> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 3 — Admin Portal: Projects and Masters

| Epic | Milestone | Stories |
|---|---|---|
| [#4](https://github.com/solutionsDigibull/BAM/issues/4) `E3` | M1 Admin + Ingestion | 12 |


**Goal:** Let Admins create projects and maintain the masters that give Cognee the reference context for each project.

**Owner:** Dev D (UI) + Dev A (API) · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 2
**Design references:** [TD §3](../design/03-end-to-end-user-flow.md) (Admin flow), [TD §7](../design/07-database-design.md) (masters tables), [TD §9.2](../design/09-frontend-design.md) (Admin screens).

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

## Design pages to read

- [TD §3. End-to-end user flow](../design/03-end-to-end-user-flow.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §7. Database design](../design/07-database-design.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#54](https://github.com/solutionsDigibull/BAM/issues/54) | `E3.1` | Projects API (create, edit, archive, assign FDEs, context notes) | A Platform | S | P0 | M1 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |
| [#55](https://github.com/solutionsDigibull/BAM/issues/55) | `E3.2` | Masters engine: types, entries, versions, scope, snapshot | A Platform | M | P0 | M1 | [#54](https://github.com/solutionsDigibull/BAM/issues/54), [#21](https://github.com/solutionsDigibull/BAM/issues/21) |
| [#56](https://github.com/solutionsDigibull/BAM/issues/56) | `E3.3` | Workbook import with frozen-structure validation | A Platform | M | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#57](https://github.com/solutionsDigibull/BAM/issues/57) | `E3.4` | Workbook export + empty template | A Platform | S | P0 | M1 | [#56](https://github.com/solutionsDigibull/BAM/issues/56) |
| [#58](https://github.com/solutionsDigibull/BAM/issues/58) | `E3.5` | Stable-ID and status extraction + status write-back into Misc | A Platform | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#59](https://github.com/solutionsDigibull/BAM/issues/59) | `E3.6` | RAG Reference → Cognee node mapping | B Knowledge/LLM | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#60](https://github.com/solutionsDigibull/BAM/issues/60) | `E3.7` | Filetype table (43, frozen, read-only) + loader/route configuration | A Platform | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#63](https://github.com/solutionsDigibull/BAM/issues/63) | `E3.8` | Master documents: upload, route by filetype, admin override | A Platform | M | P1 | M1 | [#60](https://github.com/solutionsDigibull/BAM/issues/60), [#62](https://github.com/solutionsDigibull/BAM/issues/62) |
| [#64](https://github.com/solutionsDigibull/BAM/issues/64) | `E3.9` | Admin Projects screens | D Frontend | S | P0 | M1 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#54](https://github.com/solutionsDigibull/BAM/issues/54) |
| [#65](https://github.com/solutionsDigibull/BAM/issues/65) | `E3.10` | Masters screens (tabs, tooltips, chips, RAG editor, import/export wizard) | D Frontend | L | P0 | M1 | [#56](https://github.com/solutionsDigibull/BAM/issues/56), [#58](https://github.com/solutionsDigibull/BAM/issues/58), [#64](https://github.com/solutionsDigibull/BAM/issues/64) |
| [#66](https://github.com/solutionsDigibull/BAM/issues/66) | `E3.11` | Master documents panel | D Frontend | S | P1 | M1 | [#63](https://github.com/solutionsDigibull/BAM/issues/63), [#65](https://github.com/solutionsDigibull/BAM/issues/65) |
| [#68](https://github.com/solutionsDigibull/BAM/issues/68) | `E3.12` | Runtime registry view | D Frontend | S | P1 | M3 | [#67](https://github.com/solutionsDigibull/BAM/issues/67) |

---
[← Phase 2](phase-02-identity-multi-tenancy-and-rbac.md)   [Phases index](README.md)   [Phase 4 →](phase-04-project-workspace-and-document-ingestion.md)
