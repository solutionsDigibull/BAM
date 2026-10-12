# 0012 — Testing, sandbox and certification

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-20, C-26, C-27, C-28, C-29, C-30, C-51, O-03, O-17, [TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md), [TD §19](../design/19-testing-strategy.md)

## Context

Certification is automatic, so the gates, the sandbox and the test data have to be strict enough to stand in for a human reviewer.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-20 | Certification levels | Sub-Skill (unit), Macro Skill (all subs certified) and Workflow/project (all macros certified + workflow validation) | QA H1, flow |
| C-26 | MyPy | Strict | QA J3 |
| C-27 | Certification gates | AST vs manifest + Ruff/Black/MyPy strict + security scan + ≥3 Promptfoo tests on LLM-generated data in CubeSandbox; all must pass | QA K1–K5, follow-up |
| C-28 | Sandbox | CubeSandbox on a Linux server (KVM); network access allowed (controlled) | QA K1, K4 |
| C-29 | Test data | LLM-generated | QA K2 |
| C-30 | Promptfoo authoring | Hybrid*(plan recommendation adopted)*: auto-generated deterministic assertions from the O-field + LLM-generated scenarios; FDE can review/add | QA K3 |
| C-51 | Loop testing | SBAM bounded-loop harness emulates the declarative loop in CubeSandbox; ≥3 scenarios: PASS_FIRST, ESCALATION and one of PASS_AFTER_RETRY / NO_OP / EDGE | ISA14, QA K5 |

### Open items

Defaults from [PLAN §30](../plan/07-risks-and-open-items.md). A default stands until the owner answers; a different answer becomes a new ADR that supersedes this one.

| ID | Item | Status | Default |
|---|---|---|---|
| O-03 | Code security scanner: ClamAV detects malware, not insecure Python. Add**Bandit** as SAST? | default, pending owner answer | Bandit added (non-blocking warnings, blocking HIGH) |
| O-17 | Parity of SBAM loop harness with SRIM BuLLM DMR loop semantics | default, pending owner answer | `loop_spec.json` semantics in [TD §15.2](../design/15-test-data-promptfoo-and-sandbox-design.md) |

## Consequences

- Certification runs at three levels (Sub-Skill, Macro Skill, Workflow) and only in CubeSandbox. Local-docker sandbox runs never certify.
- Gates include MyPy strict; the code-security scanner default (Bandit) is recorded above as *default, pending owner answer*.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [PLAN §30](../plan/07-risks-and-open-items.md)
- [TD §15](../design/15-test-data-promptfoo-and-sandbox-design.md), [TD §19](../design/19-testing-strategy.md)
