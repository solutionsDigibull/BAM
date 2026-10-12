# 0013 — Versioning, immutability and reuse

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-31, C-32, C-33, [TD §14](../design/14-state-machines.md), [TD §7](../design/07-database-design.md)

## Context

Certified skills are handed to SRIM. Once frozen they must stay exactly as certified, and later changes need a versioning rule.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-31 | Revocation | Not allowed | QA L1 |
| C-32 | Versioning | MAJOR.0.PATCH: 1.0.0 → 2.0.0 for major, 1.0.1, 1.0.2 … for minor | QA L2 |
| C-33 | Reuse | Unchanged Sub-/Macro Skills reuse previous certification | QA L3 |

## Consequences

- **Frozen means immutable.** A certified or frozen version is never changed or revoked; changes produce a new version (API 423, DB frozen-row trigger from #36).
- Versions follow MAJOR.0.PATCH, and unchanged Sub-/Macro Skills reuse their previous certification.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §14](../design/14-state-machines.md), [TD §7](../design/07-database-design.md)
