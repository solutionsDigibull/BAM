# 0004 — Product scope, roles and hierarchy

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-01, C-02, C-05, C-45, C-46, [TD §2](../design/02-terminology-and-hierarchy.md), [TD §3](../design/03-end-to-end-user-flow.md)

## Context

SBAM starts from an empty repository and has two kinds of users. The hierarchy decides who creates what: the Admin sets up tenants and projects, the FDE drives the pipeline, and SBAM itself produces the Workflow.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-01 | Repository | Fresh repository; nothing exists; no other project referenced | QA A2, A3 |
| C-02 | Portals and roles | Two logins:**Admin** (masters, RBAC, projects) and **FDE** (workflows). The FDE approves SOWs. Certification is automatic. No Reviewer/Approver/Certifier roles. | QA A1, B1–B5 |
| C-05 | Hierarchy | Tenant → Project (Admin creates; holds masters context, documents, knowledge and one SOW; FDEs assigned) → FDE drives the project pipeline → **SBAM generates the Workflow** from the approved SOW → Macro Skills → Sub-Skills. **The FDE never creates a Workflow.** | QA C1, C3, flow, follow-up, owner correction 6 Oct 2026 |
| C-45 | Team | 4 developers, all skills; deliver as fast as possible without missing scope | QA S1, S2 |
| C-46 | Documents | Split into plan + technical design; Markdown; detailed | QA T1–T3 |

## Consequences

- There are only two portals (Admin and FDE). There are no Reviewer, Approver or Certifier roles; SOW approval is an FDE action, and certification is automatic.
- **The FDE never creates a Workflow.** SBAM generates it from the approved SOW. No UI or API lets an FDE create one directly (golden rule).
- The documentation is split into plan and technical design, in Markdown; this folder records the decisions behind them.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [TD §2](../design/02-terminology-and-hierarchy.md), [TD §3](../design/03-end-to-end-user-flow.md)
