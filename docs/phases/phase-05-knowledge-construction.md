> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 5 — Knowledge Construction (Cognee, OKF Bundle, Graph Explorer)

| Epic | Milestone | Stories |
|---|---|---|
| [#6](https://github.com/solutionsDigibull/BAM/issues/6) `E5` | M2 Knowledge + SOW | 6 |


**Goal:** Build a per-project knowledge graph from uploads plus Admin context, and an information-preserving OKF Bundle that gives the LLM full context for the SOW.

**Owner:** Dev B · **Indicative effort:** 2 weeks · **Complexity:** L · **Depends on:** Phase 4
**Design references:** [TD §4](../design/04-system-flow.md) (T7–T9), [TD §5.3](../design/05-component-architecture.md), [TD §13](../design/13-temporal-design.md).

### Backend

- [ ] Cognee adapter with default stores (Docker volume), one dataset per project, access-control mode verified for isolation.
- [ ] Route Cognee LLM and embedding calls through Bifrost (OpenRouter).
- [ ] Knowledge Temporal workflow, started automatically when ingestion completes: add uploads + master-context snapshot → cognify → export OKF Bundle → validate → mark READY.
- [ ] OKF Bundle content ([TD §5.3](../design/05-component-architecture.md)): full normalized text chunks, entities, relations, master references, provenance (document version, page/sheet/cell) and bundle manifest + hash.
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

## Design pages to read

- [TD §4. System flow (transition detail)](../design/04-system-flow.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §13. Temporal design](../design/13-temporal-design.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#78](https://github.com/solutionsDigibull/BAM/issues/78) | `E5.1` | Cognee adapter: default stores, per-project dataset, Bifrost routing | B Knowledge/LLM | M | P0 | M2 | [#26](https://github.com/solutionsDigibull/BAM/issues/26), [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#80](https://github.com/solutionsDigibull/BAM/issues/80) | `E5.2` | Knowledge Temporal stage: add uploads + master snapshot, cognify | B Knowledge/LLM | M | P0 | M2 | [#78](https://github.com/solutionsDigibull/BAM/issues/78), [#71](https://github.com/solutionsDigibull/BAM/issues/71), [#59](https://github.com/solutionsDigibull/BAM/issues/59), [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#81](https://github.com/solutionsDigibull/BAM/issues/81) | `E5.3` | OKF bundle build + validation + coverage | B Knowledge/LLM | M | P0 | M2 | [#80](https://github.com/solutionsDigibull/BAM/issues/80) |
| [#82](https://github.com/solutionsDigibull/BAM/issues/82) | `E5.4` | Graph read API (paging, search, node detail with sources) | B Knowledge/LLM | S | P0 | M2 | [#80](https://github.com/solutionsDigibull/BAM/issues/80) |
| [#83](https://github.com/solutionsDigibull/BAM/issues/83) | `E5.5` | Knowledge Explorer (Xyflow, read-only) | D Frontend | M | P1 | M2 | [#82](https://github.com/solutionsDigibull/BAM/issues/82) |
| [#84](https://github.com/solutionsDigibull/BAM/issues/84) | `E5.6` | Knowledge status and coverage panel | D Frontend | S | P0 | M2 | [#81](https://github.com/solutionsDigibull/BAM/issues/81) |

---
[← Phase 4](phase-04-project-workspace-and-document-ingestion.md)   [Phases index](README.md)   [Phase 6 →](phase-06-sow-generation-editing-and-export.md)
