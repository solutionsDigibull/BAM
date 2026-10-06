# BAM / SBAM — Developer Documentation

SBAM (**Skills Build Acceleration Manager**) is the build, validation and certification layer of the BuLLM platform. It turns a client's project documents into a **certified, frozen Workflow**: Macro Skills (agents) made of Sub-Skills (skills), each with an ISATVON 1.4 definition, a Skill Manifest, generated FastMCP code and test evidence. SRIM, the runtime, later consumes that Workflow.

All work is tracked as **GitHub issues in [solutionsDigibull/BAM](https://github.com/solutionsDigibull/BAM/issues)**: 17 epics, 128 stories and 5 future items.

## Start here

| If you want to… | Read |
|---|---|
| Understand what we are building, in 10 minutes | [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) |
| Pick up an issue and get a PR merged | [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) |
| **Work on a phase**: its goal, tasks, exit criteria, design pages and issues | [phases/](phases/README.md) (one page per phase, 0–16) |
| See milestones, starter issues and the critical path | [ISSUE_MAP.md](ISSUE_MAP.md) |
| See **who owns which issues** (3 lanes), the waves, hand-offs and merge order | [TEAM_PLAN.md](TEAM_PLAN.md) |
| Work with Claude Code or another coding agent | [../AGENTS.md](../AGENTS.md) (`/next-issue`, `/ship`) |
| Look up a design detail referenced by an issue (`TD §x`) | [design/](design/README.md) (one page per section §1–§20) |
| Look up a task or decision referenced by an issue (`PLAN §x`, `SBAM-0xx`, `C-xx`, `O-xx`, `AR-xx`) | [plan/](plan/README.md) (index maps every PLAN § to its page) |
| See the stakeholder answers behind decisions (`QA x`) | [plan/09-stakeholder-answers.md](plan/09-stakeholder-answers.md) |
| See the source documents (ISATVON 1.4, Admin masters workbook, process doc, architecture diagrams) | [reference/](reference/) |
| Record an architecture decision | [adr/README.md](adr/README.md) |

## Folder layout

```text
docs/
├── README.md                    this index
├── ARCHITECTURE_OVERVIEW.md     what SBAM is, hierarchy, flow, stack, repo layout, golden rules
├── DEVELOPER_GUIDE.md           issue → fork → branch → PR workflow, conventions, Definition of Done
├── ISSUE_MAP.md                 milestones, starter issues, critical path, links to phase pages
├── TEAM_PLAN.md                 3-developer lanes, waves, dependency graphs, hotspots, merge order
├── team-lanes.json              issue number → lane (D1/D2/D3) + wave, read by scripts/unblocked_issues.py
├── phases/                      one page per phase (0–16): goal, tasks, exit criteria, design links, issues
├── design/                      technical design (TD), one page per section §1–§20
├── plan/                        implementation plan (PLAN): objectives, decisions, tasks, MVP, DoD, risks, traceability
├── reference/                   source inputs (ISATVON 1.4, masters workbook, process doc, diagrams)
├── adr/                         architecture decision records (created by issue E0.1)
├── spikes/                      spike write-ups (E0.9–E0.13)
└── runbooks/                    operations runbooks (E16.6)
```

## How the issue references work

Every story issue contains:

- **Spec key** (e.g. `E4.2`), which matches `ISSUE_MAP.md` and the epic's sub-issue list.
- **Plan tasks** (`SBAM-0xx`), the task IDs in the Implementation Plan §23.
- **Docs**: sections to read before starting (`TD §5.2` → [design/05-…](design/README.md), `PLAN Phase 4` → [phases/phase-04-…](phases/README.md), `C-37` → [plan/02](plan/02-confirmed-decisions.md), `O-16` → [plan/07](plan/07-risks-and-open-items.md)).
- **Phase page**: link to the phase page with the context for the whole epic.
- **Acceptance criteria**: the checklist the reviewer verifies.
- **Blocked by #n**: issues that must be merged first.

If a spec section and an issue disagree, the issue's acceptance criteria win for that issue. Raise the mismatch on the issue so the spec gets fixed.
