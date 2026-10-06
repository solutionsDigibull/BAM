> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 13 — Test Data, Promptfoo and CubeSandbox Execution

| Epic | Milestone | Stories |
|---|---|---|
| [#14](https://github.com/solutionsDigibull/BAM/issues/14) `E13` | M4 Test + Certify | 7 |


**Goal:** Prove each Sub-Skill behaves correctly under the ISATVON 1.4 bounded loop on realistic LLM-generated data inside an isolated MicroVM, with at least three passing loop scenarios.

**Owner:** Dev C + Dev A (sandbox host) · **Indicative effort:** 2.5 weeks · **Complexity:** XL · **Depends on:** Phase 12; Linux/KVM server reachable over Netbird
**Design references:** [TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md).

### Test data

- [ ] Test-data generator (`sbam-testgen`): ≥3 loop scenarios per Sub-Skill — PASS_FIRST, ESCALATION and one of PASS_AFTER_RETRY / NO_OP / EDGE — each with inputs, initial fresh-state rows (seed SQL), scripted state changes between passes (retry scenarios), files for FILE refs, mocks for API refs, and the expected terminal state and output ([TD §15.1](../design/15-test-data-promptfoo-and-sandbox-design.md)).
- [ ] Datasets versioned, hashed, flagged `synthetic`, and editable or extendable by the FDE before the run.
- [ ] Mock services for sources with network dependencies (generated mock API definitions served by a mock server container, *proposed*).

### Promptfoo

- [ ] Suite builder: deterministic assertions — output schema, terminal state vs success/escalation gates, iterations ≤ `max_iterations`, cycle time ≤ `budget_limit`, handoff rows appended with `required_fields`, expected `ui_updates` sequence; LLM-rubric only where deterministic checks are impossible.
- [ ] Minimum 3 scenarios per Sub-Skill enforced (PASS_FIRST + ESCALATION + one more); all must pass.
- [ ] Results (per case and assertion) stored and pushed as badges to the AG Grid status column.

### CubeSandbox

- [ ] `skill-runtime` image → CubeSandbox template; digest recorded in registry and manifest.
- [ ] Sandbox adapter: create MicroVM, mount bundle + `loop_spec.json` + Workflow `schema.sql` + dataset, start ephemeral PostgreSQL, run each scenario through the **bounded-loop harness** (no-op check → action → fixed check → gates, per pass, within bounds; [TD §15.2](../design/15-test-data-promptfoo-and-sandbox-design.md)), collect outputs, harness trace, handoff rows, logs and resources; destroy the VM.
- [ ] Network: egress allowed only to the mock-service network and explicitly allowlisted hosts; all egress logged (C-28).
- [ ] Local fallback `local-docker` mode for laptops, marked `NON_CERTIFYING`.
- [ ] Failure categories: IMPORT_ERROR, RUNTIME_ERROR, TIMEOUT, OOM, OUTPUT_SCHEMA_MISMATCH, ASSERTION_FAILED, EGRESS_BLOCKED, INFRA_ERROR. Failures feed the correction loop (INFRA_ERROR → retry / TESTING_BLOCKED instead).

### Tests

- [ ] A known-bad tool fails its suite (meta-test); an egress attempt to a non-allowlisted host is blocked; `local-docker` results cannot certify.

### Exit Criteria

- [ ] Every certification candidate has ≥3 passing loop scenarios (including PASS_FIRST and ESCALATION) executed by the harness in CubeSandbox on the same code hash and dataset hash.
- [ ] Sandbox runs never touch real client systems; all network access is to mocks or allowlisted hosts and is logged.

## Design pages to read

- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §15. Test data, Promptfoo and sandbox design](../design/15-test-data-promptfoo-and-sandbox-design.md)
- [TD §20. Docker and environments](../design/20-docker-and-environments.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#122](https://github.com/solutionsDigibull/BAM/issues/122) | `E13.1` | CubeSandbox host + template from skill-runtime + Netbird | A Platform | L | P0 | M3 | [#28](https://github.com/solutionsDigibull/BAM/issues/28), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#123](https://github.com/solutionsDigibull/BAM/issues/123) | `E13.2` | Sandbox adapter (cubesandbox + local-docker non-certifying) | C Build | M | P0 | M4 | [#122](https://github.com/solutionsDigibull/BAM/issues/122), [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#124](https://github.com/solutionsDigibull/BAM/issues/124) | `E13.3` | Bounded-loop harness | C Build | L | P0 | M3 | [#102](https://github.com/solutionsDigibull/BAM/issues/102), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#125](https://github.com/solutionsDigibull/BAM/issues/125) | `E13.4` | Testgen: loop scenarios, seed state, state scripts, files | B Knowledge/LLM | M | P0 | M4 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#126](https://github.com/solutionsDigibull/BAM/issues/126) | `E13.5` | Mock API service for networked sources | B Knowledge/LLM | S | P1 | M4 | [#125](https://github.com/solutionsDigibull/BAM/issues/125) |
| [#127](https://github.com/solutionsDigibull/BAM/issues/127) | `E13.6` | Promptfoo suite builder + runner + grid badges | C Build | M | P0 | M4 | [#124](https://github.com/solutionsDigibull/BAM/issues/124), [#125](https://github.com/solutionsDigibull/BAM/issues/125) |
| [#128](https://github.com/solutionsDigibull/BAM/issues/128) | `E13.7` | Test panel UI (cases + harness trace) | D Frontend | M | P0 | M4 | [#125](https://github.com/solutionsDigibull/BAM/issues/125), [#121](https://github.com/solutionsDigibull/BAM/issues/121) |

---
[← Phase 12](phase-12-ast-and-static-validation-gates.md)   [Phases index](README.md)   [Phase 14 →](phase-14-certification-versioning-freeze-and-export.md)
