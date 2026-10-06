> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 7 — SOW Approval (HITL) and Scope-Change Versioning

| Epic | Milestone | Stories |
|---|---|---|
| [#8](https://github.com/solutionsDigibull/BAM/issues/8) `E7` | M2 Knowledge + SOW | 3 |


**Goal:** Make FDE approval the hard gate before the ISATVON breakdown, and handle scope changes by versioning the SOW and invalidating only what changed.

**Owner:** Dev B + Dev D · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 6
**Design references:** [TD §14.3](../design/14-state-machines.md), [TD §4](../design/04-system-flow.md) (T12–T13).

### Backend

- [ ] Approve action (FDE): binds the exact SOW version hash, approval timestamp and FDE identity; locks that version.
- [ ] The Temporal pipeline waits for the `sow_approved` signal before the ISATVON breakdown.
- [ ] Scope change: the FDE creates a new SOW version from the approved one → edit → approve again.
- [ ] Impact analysis: compare old and new SOW sections; map changed sections to Macro/Sub-Skills via `sow_refs`; mark only affected Sub-Skills `INVALIDATED`; unchanged Sub-Skills keep their certification (C-33).
- [ ] MAJOR version increment of the Workflow on approval of a new SOW version ([TD §11.4](../design/11-manifest-design.md)).

### Frontend

- [ ] Approve button with confirmation; approved banner; "Create new SOW version" action; impact preview (which Macro/Sub-Skills will rebuild).

### Tests

- [ ] An unapproved SOW cannot start the ISATVON breakdown (API returns 409).
- [ ] Scope change on one section invalidates only the Sub-Skills that cite it.

### Exit Criteria

- [ ] No ISATVON breakdown can run from an unapproved SOW version.
- [ ] A new SOW version rebuilds only the affected Sub-Skills, and the impact preview matches the actual rebuild.

## Design pages to read

- [TD §4. System flow (transition detail)](../design/04-system-flow.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §11. Manifest design](../design/11-manifest-design.md)
- [TD §14. State machines](../design/14-state-machines.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#90](https://github.com/solutionsDigibull/BAM/issues/90) | `E7.1` | Hash-bound SOW approval + lock + pipeline signal | B Knowledge/LLM | S | P0 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87), [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#95](https://github.com/solutionsDigibull/BAM/issues/95) | `E7.2` | New SOW version + impact analysis + selective invalidation | B Knowledge/LLM | M | P1 | M3 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#94](https://github.com/solutionsDigibull/BAM/issues/94) |
| [#96](https://github.com/solutionsDigibull/BAM/issues/96) | `E7.3` | Approve UI + impact preview | D Frontend | S | P0 | M2 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#89](https://github.com/solutionsDigibull/BAM/issues/89) |

---
[← Phase 6](phase-06-sow-generation-editing-and-export.md)   [Phases index](README.md)   [Phase 8 →](phase-08-isatvon-breakdown-and-workbench.md)
