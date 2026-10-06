> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 10 — Temporal Build Orchestration

| Epic | Milestone | Stories |
|---|---|---|
| [#11](https://github.com/solutionsDigibull/BAM/issues/11) `E10` | M3 Breakdown + Build | 6 |


**Goal:** Implement the durable pipeline (parent Workflow pipeline + per-Sub-Skill build) with signals, retries, correction loops and in-app notifications, so every gate plugs into one backbone.

**Owner:** Dev C · **Indicative effort:** 1.5 weeks · **Complexity:** L · **Depends on:** Phase 9 (can start against stubs in week 3)
**Design references:** [TD §13](../design/13-temporal-design.md), [TD §14](../design/14-state-machines.md).

### Backend

- [ ] `ProjectPipeline` parent Temporal workflow (one per project): ingestion → knowledge → SOW → wait `sow_approved` → **create the Workflow** → ISATVON breakdown → wait `generate` → build `schema.sql` → child builds → Macro certification → Workflow validation & certification → freeze.
- [ ] `SubSkillBuild` child Temporal workflow: manifest → codegen → AST → static/security → test data → Promptfoo suite → CubeSandbox run → certify (with correction loop ≤2).
- [ ] Parallelism: Sub-Skills build concurrently (limit configurable, default 4), respecting `depends_on` order where outputs feed inputs.
- [ ] Signals: `sow_approved`, `generate`, `retry` (after FDE fix), `rebuild_subskills`, `cancel`. Queries: current stage, progress.
- [ ] Retry policies and timeouts ([TD §13.3](../design/13-temporal-design.md)); idempotent activities; evidence persisted before returning.
- [ ] Notification activity: N-field event → in-app notification → next step.
- [ ] `temporal_runs` mirror table for the UI; search attributes `TenantId`, `ProjectId`, `SbamWorkflowId`, `SkillId`, `SbamStage` ([TD §13.1](../design/13-temporal-design.md)).

### Tests

- [ ] Workflow-environment tests for every state path (with mocked activities), time-skipping for waits, replay tests in CI.
- [ ] Worker crash mid-build produces no duplicate evidence and resumes.

### Exit Criteria

- [ ] Every transition in [TD §14](../design/14-state-machines.md) is reachable in tests and persisted correctly.
- [ ] Running pipelines survive worker and server restarts without losing or duplicating work.

## Design pages to read

- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §12. Code generation (hybrid Jinja2 + LLM)](../design/12-code-generation.md)
- [TD §13. Temporal design](../design/13-temporal-design.md)
- [TD §14. State machines](../design/14-state-machines.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#79](https://github.com/solutionsDigibull/BAM/issues/79) | `E10.1` | ProjectPipeline skeleton with stub activities, signals and waits | C Build | M | P0 | M1 | [#40](https://github.com/solutionsDigibull/BAM/issues/40), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#107](https://github.com/solutionsDigibull/BAM/issues/107) | `E10.2` | SubSkillBuild workflow + correction loop (≤2) + retry signal | C Build | M | P0 | M2 | [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#108](https://github.com/solutionsDigibull/BAM/issues/108) | `E10.3` | Parallel build scheduler (limit, I/O ordering) | C Build | S | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#109](https://github.com/solutionsDigibull/BAM/issues/109) | `E10.4` | Notifications service + API (N = Next/Notification) | A Platform | S | P0 | M3 | [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#110](https://github.com/solutionsDigibull/BAM/issues/110) | `E10.5` | Notification bell + inbox | D Frontend | S | P0 | M3 | [#109](https://github.com/solutionsDigibull/BAM/issues/109), [#44](https://github.com/solutionsDigibull/BAM/issues/44) |
| [#111](https://github.com/solutionsDigibull/BAM/issues/111) | `E10.6` | Temporal replay tests in CI | C Build | S | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107) |

---
[← Phase 9](phase-09-sub-skill-json-manifest-generation.md)   [Phases index](README.md)   [Phase 11 →](phase-11-hybrid-code-generation-and-correction.md)
