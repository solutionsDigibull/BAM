> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §10. ISATVON 1.4 contract (Sub-Skill level)


Source: *ISATVON 1.4 for Skill Generation* (version 1.4.0, 29 June 2026), referred to as **ISA14**. Only the contract-relevant content is used here. ISATVON 1.4 is a **Declarative Bounded Iteration** format: each Sub-Skill is "a task with a fixed check". An **Action Step** is followed by an independent **Acceptance Check**, repeated within **5 mandatory loop bounds**. The loop is declarative; it is executed by the runtime (BuLLM DMR in SRIM), not by hand-written Python loops.

## 10.1 Hierarchy and IDs

```text
Approved SOW
 └── Macro Skill (agent)   id: MSnn            macro_category: "MSnn_<Name>"          e.g. MS01_Investor_Registration_Access
      └── Sub-Skill (skill) id: MSnn.m          skill_id:       "MSnn.m_<Name>"        e.g. MS01.1_Investor_Auth_Manager
           └── ISATVON 1.4 definition → Skill Manifest → Action tool + Fixed Check tool → certified
```

- `nn` = two-digit Macro Skill sequence in the Workflow; `m` = Sub-Skill sequence in the Macro; `<Name>` = PascalCase/underscore words. IDs are stable across versions.
- Macro Skills may reference an **Agents master** entry (`AG-*`). Sub-Skills may reference a **Skills master** entry (`SK-*`) and **Rules master** entries (`RULE-*`).

## 10.2 Macro Skill

```text
MacroSkill
├── macro_category: "MSnn_<Name>"        (unique in Workflow)
├── name, description                     (major module / functional area)
├── order: int
├── sow_refs: [section_id]  (min 1)
├── agent_ref: "AG-…" | null              (Agents master: role, allowed input context, required output, review gate)
└── sub_skills: [SubSkill] (min 1)
```

## 10.3 ISATVON 1.4 definition (strict)

Field names follow the ISA14 sample manifest (`isatvon_definition`). The sub-fields of each element follow the ISA14 element tables.

| Element                        | Field                      | Type                                                                         | Required                  | Content (ISA14)                                                                                                                     |
| ------------------------------ | -------------------------- | ---------------------------------------------------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **I** `i_instructions` | `statement`              | string                                                                       | ✔                        | One-line rule in the form*"Triggered by … Action: … Check: … Bound: …"* (the ISA14 sample value)                              |
|                                | `trigger`                | string                                                                       | ✔                        | What starts the skill                                                                                                               |
|                                | `action`                 | string                                                                       | ✔                        | What the skill does                                                                                                                 |
|                                | `check`                  | string                                                                       | ✔                        | What is verified after the action                                                                                                   |
|                                | `bound`                  | string                                                                       | ✔                        | The iteration bound in words (must agree with V)                                                                                    |
|                                | `user_role`              | {`level`, `persona`, `authority`}                                      | ✔                        | Seniority, profile, decision rights                                                                                                 |
|                                | `task`                   | {`goal`, `method`, `steps[]`}                                          | ✔                        | Measurable goal, primary approach, ordered steps (Trigger → Action Step → Fixed Check → Loop or Resolve)                         |
|                                | `context`                | {`background`, `topic`, `history`}                                     | ✔ (`history` optional) | Business situation, subject focus, recent events                                                                                    |
| **S** `s_source`       | `required_inputs`        | string[] (min 1)                                                             | ✔                        | Input names (become`contracts.input_schema.required`)                                                                             |
|                                | `fresh_state_parameters` | string[]                                                                     | ✔ (may be empty)         | Internal state re-read at**every** loop iteration (e.g., DB fields)                                                           |
|                                | `external_data`          | [{`name`, `kind` (FILE, API, URL), `ref`}]                             | optional                  | Websites / links / APIs / uploaded files                                                                                            |
|                                | `ml_data`                | string                                                                       | optional                  | Training datasets ("not required" for deterministic skills)                                                                         |
| **A** `a_automation`   | `orchestration_layer`    | string                                                                       | ✔                        | Routing layer, e.g.`BuLLM_DMR`                                                                                                    |
|                                | `trigger_mode`           | string                                                                       | ✔                        | e.g.`HTTP_POST_Payload_plus_Variables`                                                                                            |
|                                | `action_step`            | string                                                                       | ✔                        | The small, bounded, reversible change the system executes                                                                           |
|                                | `acceptance_check`       | string                                                                       | ✔                        | The objective, programmatic verification run after the action                                                                       |
|                                | `srim_metadata`          | string                                                                       | ✔                        | Link to execution/persistence (e.g., FastMCP engine + PostgreSQL JSONB)                                                             |
| **T** `t_tech_stack`   | `execution_layer`        | string                                                                       | ✔                        | Core engines, e.g.`FastMCP`                                                                                                       |
|                                | `action_tool`            | string                                                                       | ✔                        | MCP tool executing the core operation                                                                                               |
|                                | `fixed_check_tool`       | string                                                                       | ✔                        | **Independent** MCP tool executing validation; must differ from `action_tool`                                               |
|                                | `secondary_tools`        | string[]                                                                     | optional                  | Supporting services/providers                                                                                                       |
|                                | `gateway_routing`        | string                                                                       | optional                  | e.g.`MCP_Jungle_Gateway`                                                                                                          |
|                                | `libraries`              | [{`package`, `version_constraint?`}]                                     | ✔                        | Python libraries used by the tools. Each must exist in the SBAM runtime registry.*(SBAM build field, needed for code generation)* |
| **V** `v_variables`    | `max_iterations`         | int (1–10)                                                                  | ✔                        | Loop bound 1                                                                                                                        |
|                                | `success_gate`           | boolean expression                                                           | ✔                        | Loop bound 2 — exit with success (maps to O.success_criteria)                                                                      |
|                                | `no_op_condition`        | boolean expression                                                           | ✔                        | Loop bound 3 — skip the action entirely                                                                                            |
|                                | `escalation_condition`   | boolean expression                                                           | ✔                        | Loop bound 4 — stop and escalate (typically retries ≥`max_iterations`)                                                          |
|                                | `budget_limit`           | string (`<n>ms` or `<n>s`, optional per-cycle text)                      | ✔                        | Loop bound 5 — time budget per validation cycle                                                                                    |
|                                | `tables`                 | string[]                                                                     | ✔ (may be empty)         | Data schema: named PostgreSQL tables used (including the handoff table)                                                             |
|                                | `parameters`             | [{`name`, `type`, `value?`, `default?`, `constraints?`}]           | optional                  | Other typed parameters/constants                                                                                                    |
|                                | `secrets`                | string[]                                                                     | optional                  | Names of secrets/keys only (never values)                                                                                           |
| **O** `o_outcome`      | `json_schema`            | JSON Schema-like object                                                      | ✔                        | Exact final output format / API contract                                                                                            |
|                                | `state_handoff_file`     | {`table`, `mode`: "append_only", `required_fields[]`, `description`} | ✔                        | Mandatory append-only audit trail (e.g., iteration_pass, evidence, final scope)                                                     |
|                                | `success_criteria`       | string                                                                       | ✔                        | Acceptance condition in words; must be consistent with`success_gate`                                                              |
| **N** `n_notification` (Next + Notification) | `ui_updates`             | string[] (min 2)                                                             | ✔                        | Status messages including iteration progress (`Looping [Pass x/y]`) and the escalation message                                    |
|                                | `database_updates`       | string[]                                                                     | ✔ (may be empty)         | Writes to state/config tables                                                                                                       |
|                                | `alerts`                 | string[]                                                                     | ✔ (may be empty)         | Operational/security notifications (e.g., on escalation)                                                                            |
|                                | `next`                   | `skill_id` \| `macro_category` \| null                                      | ✔ (may be null)          | **Next** step: the next Sub-Skill in the same Agent; the last Sub-Skill names the next Agent; null after the last Agent's last Sub-Skill. *(SBAM build field; N = Next + Notification)* |

> In ISA14's sample, `i_instructions` is a single string and `s_source`, `a_automation`, `t_tech_stack`, `v_variables`, `o_outcome` and `n_notification` are objects. SBAM stores the **structured** form above and renders `i_instructions` as the sample's single string (`statement`) in the exported Skill Manifest, so the manifest stays exactly in the ISA14 shape ([§11.1](11-manifest-design.md)). The structured sub-fields are kept in `sbam_build_record.isatvon_detail`.

## 10.4 Validation rules (grid revalidation and build stage A)

1. Strict Pydantic validation: every required field is present and non-empty; no type coercion.
2. `skill_id` matches `^MS\d{2}\.\d+_[A-Za-z0-9_]+$`; `macro_category` matches `^MS\d{2}_[A-Za-z0-9_]+$`; the skill_id prefix equals its Macro's `MSnn`.
3. **I/A consistency:** `i_instructions.action` agrees with `a_automation.action_step`; `check` agrees with `acceptance_check`; `bound` mentions the `max_iterations` value. LLM-assisted consistency check plus rule checks.
4. **Decoupling:** `t_tech_stack.action_tool` ≠ `fixed_check_tool`.
5. **Five loop bounds:** all present. `max_iterations` is an integer 1–10 *(upper limit proposed)*. `budget_limit` parses to milliseconds. `success_gate`, `no_op_condition` and `escalation_condition` parse in the SBAM safe expression grammar (comparisons, `AND`/`OR`/`NOT`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `is`, literals). Every identifier they use resolves to an output field, a fresh-state parameter, an input or `max_iterations`.
6. **Contracts:** each `s_source.required_inputs` item becomes a property in `contracts.input_schema` with a type. `o_outcome.json_schema` converts to `contracts.output_schema`, which must also contain `state_handoff_id`.
7. **State handoff:** `o_outcome.state_handoff_file.table` is listed in `v_variables.tables`; `mode` is `append_only`; `required_fields` has at least 1 entry.
8. **Notifications and Next:** `ui_updates` has at least 2 messages, including one iteration-progress message and one escalation/completion message. `next` is a `skill_id` or `macro_category` and never the Sub-Skill itself. Within an Agent, `next` links all Sub-Skills into one linear chain (no branch, cycle or orphan, no other Agent's Sub-Skill); the last Sub-Skill points to the following Agent in Workflow order, or null for the last Agent.
9. **Libraries:** every `t_tech_stack.libraries` package exists in the runtime registry (PostgreSQL only; no DuckDB).
10. **Traceability:** `sow_refs` has at least 1 entry. Text fields are length-limited and control characters stripped. Text is data and is never executed as code.

## 10.5 Versioning

The ISATVON framework version (1.4) is recorded in `sbam_build_record.isatvon_framework_version`. The Sub-Skill's own version follows SBAM versioning (C-32, [§11.4](11-manifest-design.md)) and is written to `skill_metadata.version` and `governance.versioning`. A new ISATVON framework version (for example 1.5) is a contract migration with a dedicated ADR.

## 10.6 SOW structure (derived from ISATVON 1.4)

The SOW is generated so it can be broken down mechanically into ISATVON. `SowDocument` sections:

1. **Overview** — client, objective, background, topic, history (→ I.context).
2. **Users and authority** — roles, personas, decision rights (→ I.user_role).
3. **Scope** — in scope / out of scope.
4. **Modules** — one subsection per future Macro Skill (name, purpose, agent role).
5. **Features** — per module, one item per future Sub-Skill. Each item has: trigger; action; check; bound/retry policy; inputs and state to re-read; external data; tools (action vs check); tables used; success criteria; output; audit/handoff record; status messages, DB updates and alerts.
6. **Data and integrations** — sources, APIs, tables.
7. **Rules and constraints** — references to Rules master entries.
8. **Deliverables and acceptance** — outputs and acceptance criteria.
9. **Assumptions and open points.**

Every section and feature item carries `citations[]` (OKF chunk/entity ids → document + location). Feature items have stable ids, which drive Sub-Skill extraction and scope-change impact analysis.

Contract: `server/contracts/sbam_contracts/sow.py` (`SowDocument`, E0.5). Keys in order: `overview`, `users_authority`, `scope`, `modules[]` (`id` `M-…`, `name`, `purpose`, `agent_role`), `features[]` (`id` `F-…`, `module_id`, `name`, `trigger`, `action`, `check`, `bound {max_attempts 1–10, on_exhaustion}`, `inputs_state[]`, `external_data[]`, `tools {action[], check[]}` (no overlap), `tables[]`, `success_criteria[]`, `output`, `handoff`, `notifications[]`), `data_integrations`, `rules_constraints` (`rule_refs` `RULE-*`), `deliverables_acceptance`, `assumptions_open_points`. A citation is `{okf_ref_id, document_version_id (UUID), location {page?, line?, sheet?, cell?}}`. Every module has at least one feature; module and feature ids are unique. Platform fields (status, approval, hashes) live outside the document.

---
[← §9](09-frontend-design.md)   [Design index](README.md)   [§11 →](11-manifest-design.md)
