# Architecture Decision Records (ADRs)

ADRs record decisions that change how SBAM is built. Issue **E0.1** seeds this folder with the confirmed decisions (`C-xx`) and the defaults for open items (`O-xx`) from [plan/02-confirmed-decisions.md](../plan/02-confirmed-decisions.md) and [plan/07-risks-and-open-items.md](../plan/07-risks-and-open-items.md).

## When to write one

- You change a contract (ISATVON, Skill Manifest, masters, SOW, API conventions).
- You pick between options listed as an open item (`O-xx`).
- A spike (E0.9–E0.13) concludes, or you deviate from the technical design.

## How

1. Copy the template below to `NNNN-short-title.md` (next free number).
2. Open a PR titled `[ADR] <title>` that references the related issue.
3. On merge, the ADR status becomes **Accepted**. If the decision changes the specs, update the master spec files (maintainers regenerate `docs/phases`, `docs/design` and `docs/plan` from them) in the same PR.

## Template

```markdown
# NNNN — <Title>

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Related: issue #n, plan C-xx / O-xx, TD §x

## Context
What problem or open question forces a decision.

## Decision
What we will do.

## Consequences
What becomes easier/harder; follow-up issues.
```
