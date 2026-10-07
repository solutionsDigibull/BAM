> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §11. Manifest design


## 11.1 Sub-Skill manifest = ISATVON 1.4 Skill Manifest (+ SBAM build record)

The Sub-Skill manifest uses **exactly** the ISA14 Skill Manifest top-level structure, so the file SRIM receives is the format SRIM expects. SBAM adds one extension block, `sbam_build_record`, for provenance, build evidence and integrity.

The contract has two layers (`server/contracts/sbam_contracts/manifest.py`): `Isa14Manifest` is the exact ISA14 five-block shape and accepts the verbatim ISA14 sample (version `1.4.0`, `i_instructions` and `state_handoff_file` as text); it is the FDE/LLM-facing shape and rejects `sbam_build_record`. `SkillManifest` adds `sbam_build_record` and the SBAM rules: `skill_type` `Deterministic_Loopy_MCP` (O-15), status DRAFT/VALIDATING/APPROVED/SUPERSEDED, version `MAJOR.0.PATCH` equal to `governance.versioning` (C-32), UTC timestamps, APPROVED ⇒ `immutable_after_approval` (C-52), and `n_notification.next` present.

```json
{
  "skill_metadata": {
    "macro_category": "MS01_<Name>",
    "skill_id": "MS01.1_<Name>",
    "skill_name": "<human name>",
    "skill_type": "Deterministic_Loopy_MCP",
    "reusable": true,
    "version": "1.0.0",
    "status": "DRAFT",
    "owner": "<tenant / project owner>",
    "created_at": "<ISO-8601>",
    "updated_at": "<ISO-8601>"
  },
  "isatvon_definition": {
    "i_instructions": "Triggered by … Action: … Check: … Bound: …",
    "s_source": { "required_inputs": [], "fresh_state_parameters": [] },
    "a_automation": { "orchestration_layer": "BuLLM_DMR", "action_step": "", "acceptance_check": "" },
    "t_tech_stack": { "execution_layer": "FastMCP", "action_tool": "", "fixed_check_tool": "" },
    "v_variables": { "max_iterations": 3, "success_gate": "", "no_op_condition": "", "escalation_condition": "", "budget_limit": "" },
    "o_outcome": { "json_schema": {}, "state_handoff_file": { "table": "", "mode": "append_only", "required_fields": [], "description": "" }, "success_criteria": "" },
    "n_notification": { "ui_updates": [], "database_updates": [], "alerts": [], "next": "MS01.2_<Name> | MS02_<Name> | null" }
  },
  "srim_runtime_definition": {
    "orchestration": { "router": "Dify_Dynamic_Master_Router", "trigger": "HTTP_POST_Payload_plus_Variables" },
    "execution": { "engine": "FastMCP_Execution_Engine" },
    "gateway": { "route_via": "MCP_Jungle_Gateway_Reverse_Proxy" },
    "persistence": { "database": "PostgreSQL_Database_State_Config", "table": "", "action": "Execute_SQL_Queries_via_Postgres_MCP" },
    "audit": { "telemetry": "PostgreSQL_Audit_Logs" },
    "ui": { "target": "SRIM_UI_Canvas", "method": "Embedded_iFrames_Widgets" }
  },
  "contracts": {
    "input_schema": { "type": "object", "properties": {}, "required": [] },
    "output_schema": { "type": "object", "properties": { "state_handoff_id": { "type": "string" } } }
  },
  "governance": {
    "deterministic_hash": "sha256:<computed>",
    "lifecycle": { "state": "APPROVED", "approval_required_for_change": true, "immutable_after_approval": true },
    "versioning": { "major": 1, "minor": 0, "patch": 0 }
  },
  "sbam_build_record": {
    "isatvon_framework_version": "1.4",
    "isatvon_detail": "<structured I/S/A/T/V/O/N from §10.3>",
    "ids": { "tenant_id": "", "project_id": "", "workflow_id": "", "sub_skill_id": "" },
    "provenance": { "sow_version_id": "", "sow_sha256": "", "approved_at": "", "approved_by": "", "knowledge_version_id": "", "okf_sha256": "", "master_snapshot_sha256": "", "sow_refs": [] },
    "build": { "template": { "name": "", "version": "", "sha256": "" }, "runtime_image_digest": "", "libraries": [], "schema_sql_sha256": "" },
    "evidence": { "code_attempt_id": "", "bundle_sha256": "", "gates": [], "test_run_id": "", "sandbox_run_id": "", "passed_cases": 0 },
    "certification": { "state": "CERTIFIED", "certified_at": "", "reused_from": null }
  }
}
```

## 11.2 Field sources

| Block / field                                                   | Filled by                                                                                                          | Editable before certification | After certification                                   |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------- | ----------------------------------------------------- |
| `skill_metadata.macro_category`, `skill_id`, `skill_name` | LLM extraction (FDE can rename name only; ids stable)                                                              | name                          | Immutable                                             |
| `skill_metadata.skill_type`                                   | Platform default`Deterministic_Loopy_MCP` *(other values O-15)*                                                | —                            | Immutable                                             |
| `skill_metadata.reusable`, `owner`                          | Platform default; FDE may toggle`reusable`                                                                       | Yes                           | Immutable                                             |
| `skill_metadata.version`, `status`, timestamps              | Platform                                                                                                           | —                            | Immutable                                             |
| `isatvon_definition.*`                                        | LLM extraction + FDE edits + correction patches                                                                    | Yes (grid)                    | Immutable                                             |
| `srim_runtime_definition.*`                                   | Platform defaults from tenant configuration (values from ISA14 sample);`persistence.table` derived from V.tables | Admin config only             | Immutable                                             |
| `contracts.input_schema` / `output_schema`                  | Platform-derived from S and O (+`state_handoff_id`)                                                              | Recomputed                    | Immutable                                             |
| `governance.deterministic_hash`                               | Platform ([§11.5](11-manifest-design.md))                                                                                                  | Recomputed                    | Immutable                                             |
| `governance.lifecycle.state`                                  | Platform:`DRAFT` → `VALIDATING` → `APPROVED` (= SBAM certified) → `SUPERSEDED`                          | —                            | Immutable (except SUPERSEDED marker in a new version) |
| `governance.versioning`                                       | Platform (MAJOR.0.PATCH)                                                                                           | —                            | Immutable                                             |
| `sbam_build_record.*`                                         | Platform                                                                                                           | Never                         | Immutable                                             |

In ISA14, `status: "APPROVED"` with `immutable_after_approval: true` is the equivalent of SBAM "certified + frozen". SBAM sets it only through automatic certification.

## 11.3 Workflow manifest (final SBAM output)

```text
WorkflowManifest (schema_version = "sbam-workflow-manifest/1.0")
├── ids, name, objective, version (MAJOR.0.PATCH), previous_version
├── provenance: SOW version + approval, knowledge version, master snapshot
├── isatvon_framework_version: "1.4"
├── macro_skills: [{macro_category, name, agent_ref, certification_id,
│                   skills: [{skill_id, manifest_sha256, bundle_sha256, certification_id}]}]
├── schema_sql: {artifact_key, sha256}     (foundational PostgreSQL schema for all declared tables, §12.6)
├── dag: {nodes, edges}                    (Xyflow topology: macro → skills, input/output links)
├── runtime_image_digest
├── evidence_index: [{level, subject, certification_id, sha256}]
├── certification: {state: CERTIFIED | FROZEN, certified_at, frozen_at}
└── integrity: {content_sha256}
```

## 11.4 Versioning (C-32)

- Format `MAJOR.0.PATCH`, stored as string in `skill_metadata.version` and as numbers in `governance.versioning` (`minor` is always 0).
- The first freeze is 1.0.0. A new approved SOW version gives MAJOR +1 (2.0.0). A rebuild under the same SOW gives PATCH +1 (1.0.1).
- Unchanged skills (identical `isatvon_definition`, contracts, template version, runtime digest and test suite hash) reuse their certification (`sbam_build_record.certification.reused_from`) and keep their version number.
- There is no revocation. A new version supersedes the old (`governance.lifecycle.state = SUPERSEDED` in SBAM's records; the frozen file itself is never edited).

## 11.5 Hash rule

`governance.deterministic_hash` = `sha256:` + SHA-256 of the RFC 8785 canonical JSON of the manifest, excluding `governance.deterministic_hash`, `sbam_build_record.evidence` and `sbam_build_record.certification`. This matches ISA14's "TO_BE_COMPUTED_ON_SAVE". The Workflow manifest hash includes every skill's `deterministic_hash`, bundle hash and the schema SQL hash. Signing is future scope.

The Workflow `integrity.content_sha256` is the same hash over the Workflow manifest without `integrity` and `certification` (freezing does not change it). NaN, Infinity, non-UTF-8 strings and integers outside the I-JSON range are rejected before hashing. Implementation: `sbam_contracts.canonical` on the `rfc8785` library.

---
[← §10](10-isatvon-1-4-contract.md)   [Design index](README.md)   [§12 →](12-code-generation.md)
