# 0010 — ISATVON 1.4 and the Skill Manifest

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-16, C-21, C-48, C-49, C-52, C-54, O-15, O-16, O-19, O-20, [TD §10](../design/10-isatvon-1-4-contract.md), [TD §11](../design/11-manifest-design.md)

## Context

SBAM's output is defined by external contracts: ISATVON 1.4 for each Sub-Skill and the Skill Manifest that SRIM consumes.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-16 | ISATVON level | Macro Skill = agent (major module,`MSnn_<Name>`); Sub-Skill = skill (smallest implementable feature, `MSnn.m_<Name>`). **ISATVON 1.4 definition per Sub-Skill.** | QA G2, ISA14 |
| C-21 | Manifest for SRIM | The generated JSON manifest is what SRIM will use | QA H2 |
| C-48 | ISATVON contract | **ISATVON 1.4** (Declarative Bounded Iteration). I: trigger/action/check/bound + user role, task, context. S: required inputs + fresh state parameters (+ external/ML data). A: orchestration, action step, acceptance check, SRIM metadata. T: execution layer, action tool, **independent** fixed check tool (+ secondary tools, gateway). V: **5 mandatory loop bounds** (max_iterations, success_gate, no_op_condition, escalation_condition, budget_limit) + tables, secrets. O: output JSON schema + **append-only state handoff** + success criteria. N: UI updates, DB updates, alerts. ([TD §10](../design/10-isatvon-1-4-contract.md)) | ISA14 |
| C-49 | Sub-Skill manifest | Exactly the ISA14 Skill Manifest structure (`skill_metadata`, `isatvon_definition`, `srim_runtime_definition`, `contracts`, `governance`) plus one SBAM extension block `sbam_build_record` ([TD §11.1](../design/11-manifest-design.md)) | ISA14, QA H2 |
| C-52 | Manifest status mapping | SBAM certified/frozen = manifest`status: APPROVED`, `governance.lifecycle.state: APPROVED`, `immutable_after_approval: true` | ISA14 |
| C-54 | Foundational schema | A Workflow`schema.sql` (uploaded `.sql` or generated from V.tables) is validated and used in every sandbox run, and shipped in the export ("Contract-Driven Development") | ISA14 |

### Open items

Defaults from [PLAN §30](../plan/07-risks-and-open-items.md). A default stands until the owner answers; a different answer becomes a new ADR that supersedes this one.

| ID | Item | Status | Default |
|---|---|---|---|
| O-15 | Allowed`skill_type` values besides `Deterministic_Loopy_MCP` | default, pending owner answer | Only`Deterministic_Loopy_MCP` |
| O-16 | Foundational`schema.sql`: generated from V.tables, or always supplied by the FDE | default, pending owner answer | Uploaded`.sql` used as base if present; otherwise generated and validated |
| O-19 | `srim_runtime_definition` defaults (Dify DMR, MCP Jungle Gateway, SRIM UI Canvas): fixed per tenant or per skill | default, pending owner answer | Tenant-level defaults from the ISA14 sample; not LLM-generated |
| O-20 | `max_iterations` upper limit | default, pending owner answer | 1–10 |

## Consequences

- The Pydantic contracts in `server/contracts` (#19, #20) implement ISATVON 1.4 and the ISA14 Skill Manifest exactly; certified/frozen maps to manifest `status: APPROVED`.
- Defaults for open items are recorded above and marked *default, pending owner answer*.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [PLAN §30](../plan/07-risks-and-open-items.md)
- [TD §10](../design/10-isatvon-1-4-contract.md), [TD §11](../design/11-manifest-design.md)
