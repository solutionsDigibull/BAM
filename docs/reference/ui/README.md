> Docs: [Home](../../README.md) · [Design](../../design/README.md) · [TD §9 Frontend](../../design/09-frontend-design.md)

# UI reference screens

Approved screen designs from the UI team. **When a screen has a reference here, build to it**: layout, labels,
tabs, columns, empty states and actions. Behaviour (APIs, states, errors, RBAC) still comes from the issue and
TD §9. Where a reference and an issue disagree, ask on the issue before choosing.

## How to add or update a reference

1. One folder per area (`admin-masters/`, `admin-projects/`, `fde-sow/`, …). Lower-case, hyphenated file names.
2. Add one image per screen or state (`<screen>.jpeg|png`, e.g. `organizations.jpeg`, `projects-empty.png`).
   Keep the source workbook or doc next to the images when the screens are built from one.
3. Add a row to the index below: screen, route, issue, notes.
4. Replace an image in place when the design changes (git keeps the history); say what changed in the PR.

## Shared patterns (all admin references)

- **Page header:** back arrow + title + subtitle ("8 records · BAM 0.2 structure") in the top bar; the page title
  and a one-line description on the left, actions on the right (secondary **Bulk Upload**, primary **+ Add Row**).
- **Tabs with count badges:** `<Master> records (n)` · `Column definitions (n)` · `Source mapping`.
- **Toolbar:** search box `Search <Master>...` on the left, `n of m rows` on the right.
- **Grid:** AG Grid with small upper-case headers; first column is the row label (`Unit #1`, `Rule #2`, …) in muted
  text; the selected row is tinted; inline editing with a focused cell editor. Long text is truncated with an ellipsis.
- **Footer:** `<n> columns · workbook order preserved · scroll horizontally for all fields` on the left,
  `AG Grid inline editing · Enter to save · Esc to cancel` on the right, then `More columns → …` listing the
  columns off-screen.
- **Frozen data (Filetype):** read-only grid, **+ New version** instead of Add Row, Bulk Upload disabled,
  `Preview only · owner sign-off required to publish`.

## Index

### Admin masters (`admin-masters/`)

Source workbook: [`BuLLM - Manufacturing Masters v0.2.xlsx`](admin-masters/BuLLM%20-%20Manufacturing%20Masters%20v0.2.xlsx).
The screens show the README and the five BAM 0.2 masters; the workbook's extra **Tooling** tab has no screen yet
(open point on #65 / #55).

| Screen | Image | Route | Issue | Notes |
|---|---|---|---|---|
| README | [readme.jpeg](admin-masters/readme.jpeg) | `/admin/masters` (README tab) | #65 | Tabs: Overview, Column definitions, Mapping & build rules, Guidelines, Routing (43). Cards: workbook structure, column definitions, build rules, frozen-structure callout |
| Organization Master | [organizations.jpeg](admin-masters/organizations.jpeg) | `/admin/masters` (Organization) | #65 | 15 columns; rows `Unit #n`; stable IDs (ORG-EMS, ORG-CAB, ORG-CORE) in Code |
| Rules Master | [rules.jpeg](admin-masters/rules.jpeg) | `/admin/masters` (Rules) | #65 | 12 columns; rows `Rule #n`; RULE-BOM / OFFER / EXCESS / REPORT / CORE |
| Agents Master | [agents.jpeg](admin-masters/agents.jpeg) | `/admin/masters` (Agents) | #65 | 12 columns; rows `Agent #n`; AG-INTAKE / OFFER / COST / QA |
| Skills Master | [skills.jpeg](admin-masters/skills.jpeg) | `/admin/masters` (Skills) | #65 | 12 columns; rows `Skill #n`; SK-INTAKE … SK-CORE |
| Filetype Master | [filetype.jpeg](admin-masters/filetype.jpeg) | `/admin/masters` (Filetype) | #65 | 43 frozen types; tabs All (43) / Tier 1 / Tier 2 / Tier 3 / Column definitions (6); read-only |
