> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §15. Test data, Promptfoo and sandbox design


## 15.1 Test generation (C-29, C-30)

| Layer                    | Produced by                                                                           | Content                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deterministic assertions | Platform, from`contracts.output_schema`, `o_outcome.json_schema` and the V bounds | Output schema validity, enum values (e.g.,`auth_status ∈ {…}`), `state_handoff_id` present, handoff row appended with all `required_fields`, iterations ≤ `max_iterations`, cycle time ≤ `budget_limit`, terminal state consistent with `success_gate`/`escalation_condition`                                                                                                      |
| Loop scenarios (≥3)     | `sbam-testgen` LLM from I, S, A, V, O, N                                            | `PASS_FIRST` (success on pass 1), `PASS_AFTER_RETRY` (check fails, then succeeds within bounds), `ESCALATION` (never meets `success_gate` → escalates at `max_iterations`), `NO_OP` (`no_op_condition` true → no action executed), `EDGE` (boundary inputs). **Minimum 3 per Sub-Skill: `PASS_FIRST`, `ESCALATION` and one of `PASS_AFTER_RETRY`/`NO_OP`/`EDGE`.** |
| Test data                | `sbam-testgen` + platform writers                                                   | Inputs per`required_inputs`; initial **fresh-state** rows (seed SQL) per scenario; scripted state changes between iterations for retry scenarios; files (xlsx/csv/json) for `external_data` FILE refs; mock responses for API refs                                                                                                                                                         |
| FDE cases                | FDE in the test panel                                                                 | Optional extra cases; FDE can edit generated cases                                                                                                                                                                                                                                                                                                                                                   |
| N assertions             | Platform                                                                              | Expected`ui_updates` sequence emitted by the harness (e.g., `Looping [Pass 2/3]`, escalation message)                                                                                                                                                                                                                                                                                            |
| Rubric assertions        | Promptfoo`llm-rubric`                                                               | Only for free-text outputs where deterministic checks are impossible                                                                                                                                                                                                                                                                                                                                 |

Rules: at least 3 cases (as above), all must pass, data is always flagged `synthetic`, and the dataset and suite are hashed and stored.

## 15.2 Sandbox execution

```text
SubSkillBuild.run_sandbox_tests
  → adapter.create_vm(template = skill-runtime@digest, cpu, memory, timeout, network policy)
  → upload bundle + loop_spec.json + Workflow schema.sql + dataset (inputs, seed SQL, files, mocks)
  → start ephemeral PostgreSQL inside the VM, apply schema.sql, seed fresh state
  → for each case run the SBAM loop harness (emulates the declarative ISATVON 1.4 loop):
        if no_op_condition(state, inputs): record NO_OP, stop
        for pass in 1..max_iterations:
            re-read fresh_state_parameters
            call action tool (MCP)            ── within budget_limit
            call fixed check tool (MCP)       ── within budget_limit
            emit ui_update "Looping [Pass p/max]"
            if success_gate(outputs, state): record SUCCESS, stop
            if escalation_condition(...):    record ESCALATED, stop
            apply scripted state change (retry scenarios only)
        if no terminal state: record ESCALATED (bound reached)
     capture outputs, harness trace, handoff rows, stdout/stderr, resources
  → collect egress log → destroy VM
  → Promptfoo (sbam-promptfoo container) evaluates outputs + harness trace + handoff rows → results
```

The harness is SBAM's emulation of runtime looping for **testing only**. It does not replace SRIM's BuLLM DMR. Its semantics are documented in `loop_spec.json`, which is part of the manifest package, so SRIM can confirm parity (O-17).

| Concern            | Design                                                                                                                                                                                       |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host               | Linux server with KVM running CubeSandbox, reachable only over Netbird from`sbam-worker-test` (C-28, C-47)                                                                                 |
| Template           | Built from`skill-runtime` Docker image; digest pinned in registry and manifest (mechanism confirmed in the Phase 0 spike)                                                                  |
| Network            | Allowed (QA K4) but controlled: default egress only to the`sbam-mock-api` service and hosts in `runtime.network.allowlist`; DNS restricted; all connections logged to `egress_log_key` |
| Mock APIs          | `sbam-mock-api` container serves mocks generated from S.API definitions *(proposed: Prism/WireMock-style)*                                                                               |
| Limits             | Default 60 s per case, 1 vCPU, 1 GB RAM*(proposed)*; per-manifest overrides within policy                                                                                                  |
| Local mode         | `local-docker` adapter (no network except mock API, read-only FS, limits). Results are marked `NON_CERTIFYING` and the certification checklist rejects them.                             |
| Failure categories | IMPORT_ERROR, RUNTIME_ERROR, TIMEOUT, OOM, OUTPUT_SCHEMA_MISMATCH, ASSERTION_FAILED, EGRESS_BLOCKED, INFRA_ERROR                                                                             |

---
[← §14](14-state-machines.md)   [Design index](README.md)   [§16 →](16-security-and-multi-tenancy.md)
