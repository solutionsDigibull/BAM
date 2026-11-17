# 0029 — The approved Admin Masters UI becomes the masters spec ("Masters 0.3")

- Status: Proposed
- Date: 2026-11-16
- Related: [mapping report](../research/admin-masters-ui-mapping.md), [TD §7.3](../design/07-database-design.md), [TD §9.2](../design/09-frontend-design.md), [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md), issue #65

## Context

The UI team built the Admin Masters frontend as a separate app (`Admin_Masters`, Vite + Refine + AG Grid, with data in the browser and a mock login), and the manager approved it.

Its screens and columns differ from what BAM ships:

- BAM's masters use the frozen "BAM 0.2" manufacturing columns, are edited in a drawer and are versioned, and are imported only from a strict .xlsx workbook.
- The UI works differently:
  - it uses the workbook's own columns, with ISATVON for Skills;
  - Rules live per organisation, with prompts and usage notes;
  - a Tool Factory holds the tool registry plus agents and assistants;
  - it has template lists;
  - it edits inline and imports CSV through free column mapping.

## Decision

1. **The UI is the spec.** Its components are ported into the BAM Next client at `/admin/masters`. Its localStorage data provider is replaced by the BAM API through the existing proxy and session. There is no second app.
2. **Masters 0.3 = the approved workbook columns.** The column sets come from the UI's `workbook.json`. A data migration maps 0.2 payloads to 0.3; values with no 0.3 home are kept in a `legacy_0_2` object, so nothing is lost.
   - The Agents `Reference` keeps the meaning of `repo`, and the Tools `Links / Path` keeps `links_path` (both are read by 0028).
   - Filetype stays a backend catalogue for upload routing, without a tab.
3. **BAM's guarantees sit under the UI's behaviour:**
   - a cell edit is a new version with If-Match;
   - delete means RETIRED;
   - the server assigns IDs;
   - CSV and free-mapped rows go through a server-validated bulk endpoint, where a changed value on an existing ID is flagged, never overwritten;
   - documents go through ClamAV and StorageBackend.
4. **Masters stay admin-only** (`sbam-admin`). The UI's "FDE workspace" wording is renamed.
5. **The browser Onyx connector is dropped.** A server-side connector is a separate decision (a spike).
6. **Screens the UI didn't design keep BAM's behaviour** in the approved shell styling: Projects, Users & Roles, Registry, SRIM defaults and Tenants.

## Consequences

- The contracts change (D3 review), and the generated TS types change with them.
- The client masters tests and `client/e2e/admin-masters.spec.ts` are rewritten.
- New: a `master_lists` table, `POST /admin/masters/{type}/bulk`, and server-assigned IDs.
- TD §7.3.1's column table is superseded by 0.3; TD §7.3 and §9.2 are updated when the contracts land.
