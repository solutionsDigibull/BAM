> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 6 — SOW Generation, Editing and Export

| Epic | Milestone | Stories |
|---|---|---|
| [#7](https://github.com/solutionsDigibull/BAM/issues/7) `E6` | M2 Knowledge + SOW | 5 |


**Goal:** Generate a complete, cited SOW in one shot from the OKF Bundle, and let the FDE review, edit, version and export it.

**Owner:** Dev B (generation) + Dev D (TipTap) · **Indicative effort:** 1.5 weeks · **Complexity:** M · **Depends on:** Phase 5
**Design references:** [TD §4](../design/04-system-flow.md) (T10–T12), [TD §8.7](../design/08-api-design.md), [TD §9.3](../design/09-frontend-design.md).

### Backend

- [ ] `SowDocument` Pydantic model with the ISATVON 1.4-derived sections ([TD §10.6](../design/10-isatvon-1-4-contract.md)). Each feature item captures trigger, action, check, bound, inputs/state, tools, tables, success criteria, output, handoff and notifications. Every section carries citations.
- [ ] SOW Temporal workflow, started automatically after the knowledge version is READY: build context from the OKF Bundle (full bundle if it fits the model context, otherwise ranked selection per section) → one-shot generation via `sbam-sow` → validate → persist v1.
- [ ] Bounded re-ask (≤3) on schema failure; then `GENERATION_FAILED` with reason and a regenerate action.
- [ ] Versioning: every save is an immutable version with hash; diff between versions.
- [ ] Export: PDF, DOCX and Markdown renderers *(proposed: Markdown → Pandoc/WeasyPrint in worker container)*.
- [ ] Langfuse tracing and `llm_calls` provenance (model, prompt version, tokens).

### Frontend (FDE portal)

- [ ] SOW Review screen: TipTap editor, citation side panel (click opens source passage), version history and diff, regenerate, export menu.

### Tests

- [ ] Golden fixtures: SOW is schema-valid, every section cited, and all mandatory sections present.
- [ ] Prompt-injection document cannot alter the SOW structure or trigger actions.
- [ ] Exports open correctly in Word, PDF readers and Markdown.

### Exit Criteria

- [ ] For each fixture project, a complete cited SOW is generated automatically after ingestion and is editable in TipTap.
- [ ] Every SOW version is immutable, hashed and exportable to PDF/DOCX/MD.

## Design pages to read

- [TD §4. System flow (transition detail)](../design/04-system-flow.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §10. ISATVON 1.4 contract (Sub-Skill level)](../design/10-isatvon-1-4-contract.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#85](https://github.com/solutionsDigibull/BAM/issues/85) | `E6.1` | LLM layer: aliases, Pydantic AI wrapper, prompt registry, llm_calls, Langfuse | B Knowledge/LLM | M | P0 | M2 | [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#86](https://github.com/solutionsDigibull/BAM/issues/86) | `E6.2` | One-shot SOW generation stage (context assembly, re-ask, citations) | B Knowledge/LLM | L | P0 | M2 | [#85](https://github.com/solutionsDigibull/BAM/issues/85), [#81](https://github.com/solutionsDigibull/BAM/issues/81), [#22](https://github.com/solutionsDigibull/BAM/issues/22) |
| [#87](https://github.com/solutionsDigibull/BAM/issues/87) | `E6.3` | SOW versions, diff and regenerate APIs | B Knowledge/LLM | S | P0 | M2 | [#86](https://github.com/solutionsDigibull/BAM/issues/86) |
| [#88](https://github.com/solutionsDigibull/BAM/issues/88) | `E6.4` | SOW export: PDF, DOCX, Markdown | B Knowledge/LLM | S | P1 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87) |
| [#89](https://github.com/solutionsDigibull/BAM/issues/89) | `E6.5` | SOW screen: TipTap editor, citations panel, versions/diff | D Frontend | L | P0 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |

---
[← Phase 5](phase-05-knowledge-construction.md)   [Phases index](README.md)   [Phase 7 →](phase-07-sow-approval-and-scope-change-versioning.md)
