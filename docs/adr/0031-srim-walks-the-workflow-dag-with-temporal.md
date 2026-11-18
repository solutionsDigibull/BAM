# 0031 — SRIM walks the Workflow DAG with a Temporal orchestrator; Dify is not used

- Status: Accepted
- Date: 2026-11-18
- Related: epic #748, refines [0010](0010-isatvon-and-skill-manifest.md) (O-19), [0012](0012-testing-sandbox-and-certification.md) (O-17), [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) (decisions 3 and 4), [TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md), [TD §10](../design/10-isatvon-1-4-contract.md), [TD §11](../design/11-manifest-design.md)

## Context

[0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md) decided that each Workflow becomes one FastMCP
server that only **exposes tools**: for every Sub-Skill an action tool and an independent check tool. The order of
the steps stays in the manifest DAG (`next` and data edges), "not in the server", and the server calls no model at
run time. That left one question open: at run time, **who walks the DAG and runs each Sub-Skill's
action → check loop** (maximum iterations, success gate, escalation)?

The ISA14 sample names a "Dify Dynamic Master Router" for this (the O-19 default of
[0010](0010-isatvon-and-skill-manifest.md), `srim_runtime_definition.orchestration.router`). The owner decided on
18 November 2026 that **Dify is not used anywhere** and that the DAG is walked with **Temporal only**. This ADR
records that decision and what it changes. It does not design the SRIM runner itself.

## Decision

1. **The run-time master is a Temporal workflow in SRIM.** It is not in the generated image and it is not Dify.
   SRIM owns it, and it is one engine for every Workflow.
2. **What it reads.** The Workflow manifest's DAG (`next` and the data edges between Sub-Skills) and each
   Sub-Skill's loop bounds as in `loop_spec.json`: `max_iterations`, `success_gate`, `escalation_condition` and
   `budget_limit` (the V block of [TD §10](../design/10-isatvon-1-4-contract.md)).
3. **What it calls.** For each Sub-Skill, in DAG order, the action tool and then the check tool
   (`MS01_1__action`, `MS01_1__check`, the names of [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)
   decision 3), through the **Bifrost gateway** (decision 4 of 0028). It repeats the pair until the success gate
   holds, the maximum number of iterations is reached, or the escalation condition fires.
4. **The generated image stays a pure tool server.** No runner, no `run_start` or `run_status` endpoints and no
   model call inside the image. The check tool still never calls the action tool.
5. **BAM's sandbox harness stays test-only.** It emulates the loop for certification
   ([TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md)) and does not replace the SRIM runner. Its
   `loop_spec.json` semantics are the **parity contract** between the two (open item O-17): the SRIM runner must
   behave as the harness documents.
6. **The router is fixed to Temporal; it is not configurable.** The constant `TEMPORAL_ROUTER =
   "Temporal_Workflow_Orchestrator"` in the manifest contract is the **only router value BAM writes**.
   - The code that builds a manifest's `srim_runtime_definition` always writes it, whatever a tenant row holds.
     A router value stored in a tenant row is ignored by the builder and never copied into a manifest.
   - The Admin SRIM defaults `PUT` rejects any other router value with the usual problem+json 422, and the Admin
     screen shows the router read-only; the other blocks stay editable. A tenant row that still holds the legacy
     Dify router is read as before. Tenants without their own values get `SBAM_SRIM_DEFAULTS` (the Temporal
     router and the Bifrost gateway of 0028) instead of the ISA14 sample values.
   - Certification refuses a **new SBAM-built manifest** (one that has an `sbam_build_record`) whose router is
     not the constant (reason `ROUTER_NOT_TEMPORAL`). **Legacy frozen manifests and the ISA14 fixtures keep
     validating and keep their hashes**: the rule is applied only to manifests SBAM builds, and the router stays
     free text in the contract.
   - **SRIM executes from the frozen manifest and does not switch engines on this field.** It may check that the
     value equals the Temporal constant, nothing more.

## Consequences

- **Contract change, small and first.** `TEMPORAL_ROUTER` and `SBAM_SRIM_DEFAULTS` (Temporal router, Bifrost
  gateway) are what a tenant without its own values gets and what new manifests carry; `ISA14_SRIM_DEFAULTS` stays
  verbatim as the legacy ISA14 sample. Existing manifests
  and the ISA14 fixtures must keep validating, and frozen hashes must not change. The example in
  [TD §11](../design/11-manifest-design.md) shows the new default.
- **What this overrides in earlier ADRs.** [0010](0010-isatvon-and-skill-manifest.md): the O-19 default names Dify
  as the router; new manifests use Temporal (decision 6). Earlier ADRs are immutable and keep their text, and the
  plan rows they copy stay as written (the ADR coverage check requires that). This ADR is the reference for the
  point, and the index lists the refinement.
- **BAM does not run workflows.** The BAM side builds, certifies and registers; SRIM's Temporal workflow runs.
  Nothing here changes the build-time roles of 0028 decision 5 (the master there is the BAM Temporal workflow that
  builds), and no second orchestrator is added to BAM.
- **Free-text values are left alone.** The `a_automation.orchestration_layer` values in ISATVON samples and
  golden files (for example `BuLLM_DMR`) are free text per Sub-Skill and are not rewritten here.

## Not decided here (left to the SRIM team and the owner)

- The Temporal workflow and activity names, task queues, retry and timeout policies, and how a run is started
  (trigger and payload).
- How a human is asked when the escalation condition fires (the escalation UI and who answers).
- The SRIM credential and virtual key for Bifrost, and who starts the certified container (0028 leaves this to
  SRIM or operations).
- How a Workflow's UI is packaged and shown by SRIM.
- Whether Temporal runs in one cluster shared with BAM or in SRIM's own.

## Sources

- The owner's decision of 18 November 2026 (no Dify; Temporal only), [ADR 0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)
  decisions 3 and 4, [TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md) (the harness is test-only and
  does not replace SRIM's router), [TD §10](../design/10-isatvon-1-4-contract.md) (the loop bounds).
