# 0009 — SOW, scope change and grid editing

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-14, C-15, C-17, C-18, C-19, [TD §3](../design/03-end-to-end-user-flow.md), [TD §9](../design/09-frontend-design.md)

## Context

The SOW is the hand-off point between knowledge and build: it is generated, reviewed by a human, approved, and later changed when scope changes.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-14 | SOW | One-shot generation → HITL review/edit in TipTap → FDE approves. Citations mandatory. Export PDF/DOCX/MD. SOW structure is derived from ISATVON 1.4 ([TD §10.6](../design/10-isatvon-1-4-contract.md)). | QA F1–F4 |
| C-15 | Scope change | New SOW version; only affected Macro/Sub-Skills are invalidated and rebuilt | QA F5 |
| C-17 | Excel | Export only (no re-upload) | QA G3 |
| C-18 | N field | "Next / Notification": notify in-app, then move to the next step | QA G4, Q2 |
| C-19 | Grid editing | FDE only; no manual from-scratch rows; no real-time multi-user editing | QA G5, G6 |

## Consequences

- SOW generation is one-shot, followed by human review and editing. A scope change creates a new SOW version, and only the affected Macro/Sub-Skills are rebuilt.
- Excel is export only; grid editing is FDE-only with no manual rows from scratch.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §3](../design/03-end-to-end-user-flow.md), [TD §9](../design/09-frontend-design.md)
