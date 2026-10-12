# 0011 — Code generation and correction

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-22, C-23, C-24, C-50, [TD §12](../design/12-code-generation.md)

## Context

Each Sub-Skill becomes runnable code. The generation approach, the artifact type and what the LLM may change all needed fixing.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-22 | Code generation | **Hybrid**: Jinja2 skeleton (FastMCP server, imports, Pydantic I/O models from manifest) + LLM-written function bodies; AST validates against the manifest | QA I1, follow-up |
| C-23 | Artifact type | FastMCP tools only | QA I3 |
| C-24 | Correction | LLM may fix the ISATVON JSON**and** edit Python; 2 automatic attempts, then back to the FDE | QA I4, I5 |
| C-50 | Generated code | Per Sub-Skill: Action tool + independent Fixed Check tool (no cross-imports), state-handoff writer (insert-only), fresh-state reader;**no manual retry loops** because iteration is declarative | ISA14 |

## Consequences

- Code is a Jinja2 skeleton with LLM-written bodies, as FastMCP tools only.
- Generated code has **no manual retry loops**, because iteration is declarative (golden rule, C-50). Certification gates (C-27) are owned by [ADR 0012](0012-testing-sandbox-and-certification.md).

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §12](../design/12-code-generation.md)
