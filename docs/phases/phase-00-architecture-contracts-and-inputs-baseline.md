> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 0 — Architecture, Contracts and Inputs Baseline

| Epic | Milestone | Stories |
|---|---|---|
| [#1](https://github.com/solutionsDigibull/BAM/issues/1) `E0` | M0 Foundation | 14 |


**Goal:** Freeze the contracts and collect the missing inputs (masters schema, ISATVON sheet, SOW template) so the four developers can build in parallel without rework.

**Owner:** Tech lead + all · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** — · **Blocks:** everything
**Design references:** [TD §2](../design/02-terminology-and-hierarchy.md) (terminology), [TD §10](../design/10-isatvon-1-4-contract.md) (ISATVON), [TD §11](../design/11-manifest-design.md) (manifests), [TD §14](../design/14-state-machines.md) (state machines).

### Inputs (received)

- [ ] Convert the consolidated masters workbook (MSC, frozen BAM 0.2 structure) into six JSON Schemas (README, Organization, Rules, Agents, Skills, Filetype), the RAG Reference JSON schema, stable-ID/status extraction rules and fixtures from its rows ([TD §7.3.1](../design/07-database-design.md)).
- [ ] Convert ISATVON 1.4 (ISA14) into the strict Pydantic contract ([TD §10.3](../design/10-isatvon-1-4-contract.md)) and the Skill Manifest model ([TD §11.1](../design/11-manifest-design.md)); use the ISA14 sample manifest (MS01.1) as the golden fixture.
- [ ] Freeze the SOW structure derived from ISATVON 1.4 ([TD §10.6](../design/10-isatvon-1-4-contract.md)).

### Contracts to freeze

- [ ] ISATVON 1.4 Sub-Skill contract ([TD §10.3–10.4](../design/10-isatvon-1-4-contract.md)): fields, the 5 loop bounds, the safe expression grammar for gates, required/optional, validation rules.
- [ ] Macro Skill (agent) structure and Sub-Skill dependency rules ([TD §10.2](../design/10-isatvon-1-4-contract.md)).
- [ ] Skill Manifest (ISA14 structure + `sbam_build_record`), Workflow manifest and the hash rule ([TD §11](../design/11-manifest-design.md)).
- [ ] Loop-harness semantics (`loop_spec.json`) shared with the SRIM owners for parity (O-17).
- [ ] State machines for Workflow, Document, SOW, Sub-Skill, Macro Skill and Workflow certification ([TD §14](../design/14-state-machines.md)).
- [ ] Versioning rules for MAJOR.0.PATCH: what is major and what is minor ([TD §11.4](../design/11-manifest-design.md)).
- [ ] API conventions ([TD §8.1](../design/08-api-design.md)) and the generated TS type pipeline.
- [ ] Keycloak tenancy model: one realm with Organizations, or a realm per tenant (Section 30, O-05).
- [ ] Jinja2 skeleton contract: action/check tool modules, protected regions, LLM body slots, handoff writer, allowed imports ([TD §12](../design/12-code-generation.md)).

### Spikes (time-boxed, parallel)

- [ ] Cognee with default stores: per-project dataset isolation, Admin-context injection, LLM/embedding calls routed via Bifrost/OpenRouter.
- [ ] OKF Bundle: export from Cognee that preserves full content, entities, relations and provenance; measure size against model context.
- [ ] CubeSandbox on the Linux/KVM server: template from a Docker image, run a FastMCP tool, controlled egress, start latency.
- [ ] Hybrid codegen: Jinja2 skeleton + LLM bodies (Pydantic AI) → action + check tools + handoff + AST checks on the ISA14 sample skill and one MSC-domain skill (e.g., SK-BOM scrub).
- [ ] Promptfoo against a FastMCP tool executed in CubeSandbox.
- [ ] Keycloak + Next.js (Auth.js) + FastAPI JWT validation with tenant claim.

### Exit Criteria

- [ ] ISATVON v1, manifest v1 and state-machine documents are approved and committed as JSON Schemas/ADRs.
- [ ] Master schemas + demo rows, the ISA14 sample manifest and the SOW structure are in the repository as fixtures.
- [ ] All six spikes have written results, and no blocking unknown remains for Phases 1–5.
- [ ] Every developer knows their stream for the first three weeks (Section 24).

## Design pages to read

- [TD §2. Terminology and hierarchy](../design/02-terminology-and-hierarchy.md)
- [TD §5. Component architecture](../design/05-component-architecture.md)
- [TD §7. Database design](../design/07-database-design.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §10. ISATVON 1.4 contract (Sub-Skill level)](../design/10-isatvon-1-4-contract.md)
- [TD §11. Manifest design](../design/11-manifest-design.md)
- [TD §12. Code generation (hybrid Jinja2 + LLM)](../design/12-code-generation.md)
- [TD §14. State machines](../design/14-state-machines.md)
- [TD §15. Test data, Promptfoo and sandbox design](../design/15-test-data-promptfoo-and-sandbox-design.md)
- [TD §16. Security and multi-tenancy](../design/16-security-and-multi-tenancy.md)
- [TD §20. Docker and environments](../design/20-docker-and-environments.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#18](https://github.com/solutionsDigibull/BAM/issues/18) | `E0.1` | ADRs for confirmed decisions and open-item defaults | A Platform | S | P0 | M0 | — |
| [#19](https://github.com/solutionsDigibull/BAM/issues/19) | `E0.2` | ISATVON 1.4 Pydantic contract + gate-expression grammar | C Build | L | P0 | M0 | — |
| [#20](https://github.com/solutionsDigibull/BAM/issues/20) | `E0.3` | Skill Manifest + Workflow manifest models and RFC 8785 hashing | C Build | M | P0 | M0 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#21](https://github.com/solutionsDigibull/BAM/issues/21) | `E0.4` | Admin masters schemas (BAM 0.2) + RAG Reference + stable-ID/status rules | B Knowledge/LLM | M | P0 | M0 | — |
| [#22](https://github.com/solutionsDigibull/BAM/issues/22) | `E0.5` | SowDocument model (ISATVON 1.4-derived sections) | B Knowledge/LLM | S | P0 | M0 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#23](https://github.com/solutionsDigibull/BAM/issues/23) | `E0.6` | State-machine definitions as code (project pipeline, document, SOW, Workflow, macro, sub-skill) | A Platform | S | P0 | M0 | — |
| [#24](https://github.com/solutionsDigibull/BAM/issues/24) | `E0.7` | API conventions + problem+json error schema | A Platform | S | P0 | M0 | — |
| [#25](https://github.com/solutionsDigibull/BAM/issues/25) | `E0.8` | Golden fixture pack | B Knowledge/LLM | S | P0 | M0 | — |
| [#26](https://github.com/solutionsDigibull/BAM/issues/26) | `E0.9` | Spike: Cognee default stores + OKF bundle | B Knowledge/LLM | M | P0 | M0 | — |
| [#28](https://github.com/solutionsDigibull/BAM/issues/28) | `E0.10` | Spike: CubeSandbox on Linux/KVM host | A Platform | M | P0 | M0 | [#27](https://github.com/solutionsDigibull/BAM/issues/27) |
| [#29](https://github.com/solutionsDigibull/BAM/issues/29) | `E0.11` | Spike: hybrid codegen (Jinja2 skeleton + LLM bodies) on MS01.1 | C Build | M | P0 | M0 | [#20](https://github.com/solutionsDigibull/BAM/issues/20) |
| [#30](https://github.com/solutionsDigibull/BAM/issues/30) | `E0.12` | Spike: Promptfoo against a FastMCP tool | C Build | S | P0 | M0 | — |
| [#31](https://github.com/solutionsDigibull/BAM/issues/31) | `E0.13` | Spike: Keycloak Organizations + Auth.js + FastAPI JWT | A Platform | S | P0 | M0 | — |
| [#27](https://github.com/solutionsDigibull/BAM/issues/27) | `E0.14` | Prerequisites: GHCR, OpenRouter key, KVM host, dev VM, Netbird | A Platform | S | P0 | M0 | — |

---
   [Phases index](README.md)   [Phase 1 →](phase-01-repository-foundation-and-containerized-dev-stack.md)
