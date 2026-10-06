# BAM / SBAM — Developer Documentation

SBAM (**Skills Build Acceleration Manager**) is the build, validation and certification layer of the BuLLM platform. It turns a client's project documents into a **certified, frozen Workflow**: Macro Skills (agents) made of Sub-Skills (skills), each with an ISATVON 1.4 definition, a Skill Manifest, generated FastMCP code and test evidence. SRIM, the runtime, later consumes that Workflow.

All work is tracked as **GitHub issues in [solutionsDigibull/BAM](https://github.com/solutionsDigibull/BAM/issues)**: 17 epics, 128 stories and 5 future items.

## Start here

| If you want to… | Read |
|---|---|
| Understand what we are building, in 10 minutes | [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) |
| Pick up an issue and get a PR merged | [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) |
| See every issue by milestone, stream and dependency | [ISSUE_MAP.md](ISSUE_MAP.md) |
| Look up a design detail referenced by an issue (`TD §x`) | [specs/SBAM_Technical_Design.md](specs/SBAM_Technical_Design.md) |
| Look up a phase, task or decision referenced by an issue (`PLAN §x`, `SBAM-0xx`, `C-xx`, `O-xx`, `AR-xx`) | [specs/SBAM_Implementation_Plan.md](specs/SBAM_Implementation_Plan.md) |
| See the stakeholder answers behind decisions (`QA x`) | [specs/SBAM_Plan_Open_Questions.md](specs/SBAM_Plan_Open_Questions.md) |
| See the source documents (ISATVON 1.4, Admin masters workbook, process doc, architecture diagrams) | [reference/](reference/) |
| Record an architecture decision | [adr/README.md](adr/README.md) |

## Folder layout

```text
docs/
├── README.md                    this index
├── ARCHITECTURE_OVERVIEW.md     what SBAM is, hierarchy, flow, stack, repo layout, golden rules
├── DEVELOPER_GUIDE.md           issue → fork → branch → PR workflow, conventions, Definition of Done
├── ISSUE_MAP.md                 all issues by milestone/stream, critical path, starter issues
├── specs/                       authoritative plan + technical design (referenced by every issue)
├── reference/                   source inputs (ISATVON 1.4, masters workbook, process doc, diagrams)
├── adr/                         architecture decision records (created by issue E0.1)
├── spikes/                      spike write-ups (E0.9–E0.13)
└── runbooks/                    operations runbooks (E16.6)
```

## How the issue references work

Every story issue contains:

- **Spec key** (e.g. `E4.2`), which matches `ISSUE_MAP.md` and the epic's sub-issue list.
- **Plan tasks** (`SBAM-0xx`), the task IDs in the Implementation Plan §23.
- **Docs**: sections to read before starting (`TD §5.2`, `PLAN Phase 4`, `C-37`, `O-16` …).
- **Acceptance criteria**: the checklist the reviewer verifies.
- **Blocked by #n**: issues that must be merged first.

If a spec section and an issue disagree, the issue's acceptance criteria win for that issue. Raise the mismatch on the issue so the spec gets fixed.
