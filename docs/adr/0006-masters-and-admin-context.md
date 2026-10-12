# 0006 — Masters and admin context

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-06, C-55, O-18, O-21, O-22, O-23, O-24, [TD §7](../design/07-database-design.md)

## Context

Admins maintain the masters (README plus five masters, BAM 0.2 structure) that give every project its business context. Several details of the workbook were open questions.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-06 | Masters | README + five masters (**Organization, Rules, Agents, Skills, Filetype**), tenant-scoped, used as reference context for Cognee/OKF with Organization as graph root. **Frozen BAM 0.2 structure** from MSC: exact tab names, column names/order, 3 header rows, row labels, 43 frozen filetypes. Stable IDs and status are read from inside the rows (no extra columns). RAG Reference JSON drives graph node type and parent. Admin imports/exports the workbook in this layout ([TD §7.3.1](../design/07-database-design.md)). | QA C2–C4, MSC |
| C-55 | Master documents | Admins can attach context files to masters; each is routed to a master by the MSC Filetype default route (intent class), and the Admin confirms or overrides the route ([TD §7.3.2](../design/07-database-design.md)) | MSC README §7, §9 |

### Open items

Defaults from [PLAN §30](../plan/07-risks-and-open-items.md). A default stands until the owner answers; a different answer becomes a new ADR that supersedes this one.

| ID | Item | Status | Default |
|---|---|---|---|
| O-18 | Organization columns 7–11 | Resolved | **Resolved:** MSC has all 14 columns (Customers, Offerings, Competencies, Data Sources, Systems/Tools …) |
| O-21 | Intent classifier for master documents: MSC README proposes Jev / Laya ("accuracy untested") | default, pending owner answer | MVP routes by Filetype default route + Admin confirmation; an LLM classifier via Bifrost with a confidence threshold is optional; Jev/Laya evaluated later |
| O-22 | Status inside Misc cells (no Status column in BAM 0.2) | default, pending owner answer | Parse`Status: <value>`; missing = PROPOSED; UI writes back into Misc |
| O-23 | Who may change the frozen Filetype table | default, pending owner answer | Only super-admin, through a new workbook version with owner sign-off |
| O-24 | BAM 0.2 sample rows (Unit/Rule/Agent/Skill#1) contain mock values (e.g., Rocketreach, Raytheon) | default, pending owner answer | Imported as`ILLUSTRATIVE`; excluded from authoritative Cognee context |

## Consequences

- Masters follow the frozen BAM 0.2 structure (TD §7.3.1); master documents are context files attached by Admins.
- Defaults for open items are recorded above and marked *default, pending owner answer*. An owner answer that differs becomes a new ADR that supersedes this one.

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [PLAN §30](../plan/07-risks-and-open-items.md)
- [TD §7](../design/07-database-design.md)
