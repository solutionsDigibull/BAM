> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Technical Design (TD) — index

The technical design split into one page per section. Issues reference sections as `TD §n` or `TD §n.m`; find the page by its first number below.


**Companion to:** `SBAM_Implementation_Plan.md` (phases, tasks, timeline). Decisions are referenced as **C-xx** ([plan [§3](03-end-to-end-user-flow.md)](../plan/02-confirmed-decisions.md)), open items as **O-xx** ([plan §30](../plan/07-risks-and-open-items.md)).
**Status:** Baseline v4 (5 Oct 2026). It includes the consolidated admin masters workbook (*BuLLM – Admin Masters (One Sheet) – Consolidated.xlsx*, **MSC**, BAM 0.2 structure frozen 5 Oct 2026; it supersedes the earlier Manufacturing Template) and *ISATVON 1.4 for Skill Generation* (**ISA14**). The SOW structure is derived from ISATVON 1.4 ([§10.6](10-isatvon-1-4-contract.md)).
**Audience:** The four SBAM developers. Each section is written so one developer can build a module without waiting for clarifications.



| § | Page |
|---|---|
| 1 | [How to use this document](01-how-to-use-this-document.md) |
| 2 | [Terminology and hierarchy](02-terminology-and-hierarchy.md) |
| 3 | [End-to-end user flow](03-end-to-end-user-flow.md) |
| 4 | [System flow (transition detail)](04-system-flow.md) |
| 5 | [Component architecture](05-component-architecture.md) |
| 6 | [Repository structure](06-repository-structure.md) |
| 7 | [Database design](07-database-design.md) |
| 8 | [API design](08-api-design.md) |
| 9 | [Frontend design (Next.js + Refine)](09-frontend-design.md) |
| 10 | [ISATVON 1.4 contract (Sub-Skill level)](10-isatvon-1-4-contract.md) |
| 11 | [Manifest design](11-manifest-design.md) |
| 12 | [Code generation (hybrid Jinja2 + LLM)](12-code-generation.md) |
| 13 | [Temporal design](13-temporal-design.md) |
| 14 | [State machines](14-state-machines.md) |
| 15 | [Test data, Promptfoo and sandbox design](15-test-data-promptfoo-and-sandbox-design.md) |
| 16 | [Security and multi-tenancy](16-security-and-multi-tenancy.md) |
| 17 | [Observability and notifications](17-observability-and-notifications.md) |
| 18 | [Error handling matrix](18-error-handling-matrix.md) |
| 19 | [Testing strategy](19-testing-strategy.md) |
| 20 | [Docker and environments](20-docker-and-environments.md) |
