# Architecture Decision Records (ADRs)

ADRs record decisions that change how SBAM is built. Issue **E0.1** seeds this folder with the confirmed decisions (`C-xx`) and the defaults for open items (`O-xx`) from [plan/02-confirmed-decisions.md](../plan/02-confirmed-decisions.md) and [plan/07-risks-and-open-items.md](../plan/07-risks-and-open-items.md).

## Index

| ADR | Title | Status | Plan ids |
|---|---|---|---|
| [0001](0001-cognee-okf-config.md) | Cognee + OKF configuration (spike E0.9) | Accepted | — |
| [0002](0002-keycloak-tenancy.md) | Keycloak tenancy: one realm with Organizations (spike E0.13) | Accepted | resolves O-05 |
| [0003](0003-sync-sqlalchemy-psycopg.md) | Database access with sync SQLAlchemy 2 and psycopg 3 (#36) | Accepted | — |
| [0004](0004-product-scope-roles-hierarchy.md) | Product scope, roles and hierarchy | Accepted | C-01, C-02, C-05, C-45, C-46 |
| [0005](0005-identity-and-tenancy.md) | Identity and tenancy | Accepted | C-03, C-04, O-05 |
| [0006](0006-masters-and-admin-context.md) | Masters and admin context | Accepted | C-06, C-55, O-18, O-21, O-22, O-23, O-24 |
| [0007](0007-document-ingestion.md) | Document ingestion | Accepted | C-07, C-08, C-09, C-10, C-11, C-53 |
| [0008](0008-knowledge-cognee-okf.md) | Knowledge: Cognee and OKF | Accepted | C-12, C-13 |
| [0009](0009-sow-scope-change-and-grid.md) | SOW, scope change and grid editing | Accepted | C-14, C-15, C-17, C-18, C-19 |
| [0010](0010-isatvon-and-skill-manifest.md) | ISATVON 1.4 and the Skill Manifest | Accepted | C-16, C-21, C-48, C-49, C-52, C-54, O-15, O-16, O-19, O-20 |
| [0011](0011-code-generation-and-correction.md) | Code generation and correction | Accepted | C-22, C-23, C-24, C-50 |
| [0012](0012-testing-sandbox-and-certification.md) | Testing, sandbox and certification | Accepted | C-20, C-26, C-27, C-28, C-29, C-30, C-51, O-03, O-17 |
| [0013](0013-versioning-immutability-and-reuse.md) | Versioning, immutability and reuse | Accepted | C-31, C-32, C-33 |
| [0014](0014-platform-and-infrastructure.md) | Platform and infrastructure | Accepted | C-25, C-34, C-35, C-36, C-37, C-38, C-39, C-40, C-47 |
| [0015](0015-llm-gateway.md) | LLM gateway | Accepted | C-41 |
| [0016](0016-audit-observability-and-nfrs.md) | Audit, observability and NFRs | Accepted | C-42, C-43, C-44 |
| [0017](0017-authorization-chain.md) | Authorization chain: policies, ports and token leeway (#48) | Accepted | refines 0002 |
| [0018](0018-llm-wrapper.md) | LLM wrapper: httpx, Langfuse REST, replay cache in `llm_calls` (#85) | Accepted | refines 0015 |
| [0019](0019-sow-editor-format-and-regenerate.md) | SOW editor format (SBAM TipTap shape) and regenerate (#87) | Accepted | — |
| [0020](0020-filetype-platform-versions.md) | Filetype rows are platform data, changed by a super-admin workbook version (#257) | Accepted | refines 0006 (O-23) |

Every C-01…C-55 is owned by exactly one ADR (a row in its Decision table, copied verbatim from PLAN §3). Each open item carries its PLAN §30 default, marked *default, pending owner answer* until the owner decides. Check with `python docs/adr/check_coverage.py`; a unit test runs it in CI.

## Changing a decision

Accepted ADRs are immutable. To change one, or when an owner answers an open item differently from its default:

1. Write a new ADR (next free number) whose header says `- Supersedes: NNNN`, with the new decision and why.
2. In the old ADR change **only** its Status line to `- Status: Superseded by MMMM`. Leave its Decision text as it was.
3. Copy the affected plan rows (`| C-xx |` / `| O-xx |`) into the new ADR. A superseded ADR no longer counts as an owner, so each id still has exactly one. Update this index.

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
