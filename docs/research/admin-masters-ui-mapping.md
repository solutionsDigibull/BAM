# Approved Admin Masters UI → BAM admin: screen and field mapping

- **Date:** 2026-11-16.
- **Source:** `D:\SBAM\Admin_Masters`, repo ParvithaCGowda29/Admin_Masters, last commit `40661cd`.
- **Status:** the manager has approved this UI, so BAM adapts to it.
- **Decision record:** [ADR 0029](../adr/0029-approved-masters-ui.md).

## 1. What the UI is

- **Stack:** Vite + React 18 + `@refinedev/core` v4 + AG Grid Community 34.
- **No backend.** Rows are kept in `localStorage` (`bullm-masters-v2-<hash>`), and the seed data is `src/workbook.json`.
- **Login is a mock** that works in dev only (`src/auth/session.ts`). In a production build it fails closed.
- **No router.** There are only two paths: `/login` and `/`. Every other view is React state in `src/App.tsx`.

## 2. Screens, one by one, mapped to BAM

| # | UI screen or feature (file) | What a user can do | BAM today | Fit |
|---|---|---|---|---|
| 1 | `/login` (`auth/LoginPage.tsx`) | Email + password, "Keep me signed in", show/hide password, product-area highlights | `/login-admin` with Keycloak, plus dev sign-in buttons | Swap: use Keycloak and keep the UI's look |
| 2 | Header account menu | "Switch to Pipeline" (`localhost:5174`), Logout, light/dark toggle | PortalShell's "FDE portal" switch, Logout | ✅ concept |
| 3 | **Masters** parent (overview) and the `i` popovers | Workbook README rows; per-tab summary and field guide | Masters → README tab; header tooltips | ✅ (README moves into popovers) |
| 4 | **Organisations** grid | Inline edit (multiline), add N rows (right-click), delete row or selection, search with history, facet filters (Company / Type, Location, Certifications). Link-only cells for Data Sources, Customers, Systems, Assets. Template download in 4 column headers | Masters → Organization: antd table, edit drawer, new versions | ⚠ Columns differ (§3.1); inline edit and delete need mapping (§4) |
| 5 | Organisations → **Rules Engine** (`OrganizationRules.tsx`, `orgRuleData.ts`) | Workflow map built from the Skills `N · Next` chains; rule counts per skill and subskill. Rule cards show type, status, applies-to, linked skills, prompts, acts-on, and FDE / client-representative usage. Rule editor drawer: Rule tab and Prompts tab (one system prompt, many micro prompts). Add rule (`RULE-CUSTOM-nnn`); link or unlink skills; jump to a skill | Masters → Rules: a flat manufacturing table | ❌ **Unique**: no per-org rules, prompts or usage notes in BAM |
| 6 | **Skills** grid (ISATVON) | Single-click edit in place, auto `SK-nn` IDs, add N, duplicate or delete a row, chips for References and Tech stack, "Import" button | Masters → Skills: manufacturing columns | ⚠ Schema differs. The Import button is **dead**: it picks a file and does nothing |
| 7 | **Tool Factory** (the Tools sheet) | 173-row registry; A–Z category toggles (External, Internal and Analytical tools merged into "Tools", API and MCP deduped); a BuLLM "CREDIBLE" badge seal on the ID | The TOOLING master type exists, **with no screen** (#65). `/admin/registry` is the Python package allowlist, a different thing | ❌ UI is unique; backend is partial |
| 8 | Tool Factory → **Agents / Assistants** toggles | The Agents sheet split by `Kind`; Type pill (Internal / External / Proposed); Reference, Source and Tools chips; Model | Masters → Agents | ⚠ Schema differs; the **Assistant** kind is new |
| 9 | Tool Factory → **Import** (`ImportAgents.tsx`) | Drop an xlsx, csv or **json** file, review what was found, tick rows, import with `AG-`/`AS-` IDs. External registry agents are also copied into Agents once | None | ❌ Unique |
| 10 | **Add files** (`ExtractFlow.tsx`, `bulkImport.ts`), in 4 steps: Source, Permission, Fetch files, Mapping | Pick files, a folder or a zip (.xlsx .csv .zip .pdf .dxf .dwg .step .docx .md .txt .eml .msg). Tick which files may be read. Each table gets an auto-suggested target tab with a match % and per-column dropdowns; anything under 60% waits unticked. Imports are appended, exact repeats skipped, and a changed value on an existing ID is flagged | Import workbook wizard (strict .xlsx: validate, then upsert, with a structure diff, ClamAV and zip-bomb limits), and the Documents tab (upload → scan → parse → route) | ⚠ Different model: free mapping against a strict frozen template |
| 11 | Add files → **Onyx** (`onyx.ts`, `OnyxSettings.tsx`) | A server URL and an API token saved in localStorage; files are sent from the browser to `{onyx}/api/user/projects/file/upload` | None (BAM uses tusd, ClamAV, StorageBackend and Cognee) | 🚨 Security issue (§5). **Removed** in the port |
| 12 | Add files → SharePoint / Google Drive cards | Disabled ("Not connected") | None | Keep them disabled |
| 13 | **Add templates** (`TemplatePicker`, `TemplateUpload`, `TemplateListView`) | 4 lists: Employees, Customers / Audience, Assets (Hardware), Systems / tools (Software). View and edit a list; upload a .csv that replaces it completely; download from the Organisations column header | None | ❌ Unique (`master_lists`, §4) |
| 14 | Search (`SearchCommand.tsx`) and facets (`SortFilter.tsx`, `masterUi.ts`) | Exact match per word plus Fuse fuzzy match, with highlights; history of up to 12 terms per tab in localStorage; facet chips (OR within a facet, AND across facets) | Search box on each tab | Client-side only. Move it as is |
| 15 | Removed in the UI | The Filetype tab (dropped 2026-11-02) and the Rules / Agents nav rows | Filetype tab (frozen, 43 rows) | Keep Filetype in the backend because upload routing uses it; drop the tab |
| — | Not designed in the UI | — | Projects, Users & Roles, Registry, SRIM defaults, Tenants, Masters → Documents | Keep the BAM screens and restyle them in the approved shell |

## 3. Field mapping per master: UI header → BAM 0.2 payload key

The UI headers become the Masters 0.3 columns. "new" means BAM has no such key. Rows marked "?" are judgement calls to confirm while coding.

### 3.1 Organization (UI has 14 columns; BAM 0.2 has 14 fields)

| UI header | BAM 0.2 key |
|---|---|
| Org ID | `code` (stable ID) |
| Company / Type | `org_name` + `type` (BAM keeps them as two fields) |
| Brand Properties | new |
| Location | `location` |
| Certifications | `certifications` |
| Company Segment / Niche / Offerings | `offerings` (and `org_details`?) |
| Customers / Audience | `customers` (link-only list) |
| Employees / Job Role | `employees` |
| Data Sources | `data_sources` (path only) |
| Systems / tools (Software) | `systems_tools` |
| Assets (Hardware) | `assets` |
| FDE Notes | `misc_audit` (notes part; BAM also stores the status there) |
| Documents (hidden) | new; link to `master_documents` |
| Artifacts (hidden) | new |

BAM fields with no UI column: `org_details`, `competencies`.

### 3.2 Rules (UI has 9 columns plus extras; BAM 0.2 has 11 fields)

| UI header | BAM 0.2 key |
|---|---|
| Rule ID | stable ID (BAM: the `RULE-x:` prefix of `domain_rule`) |
| Rule / Type / Description | `domain_rule` |
| Source / approval / evidence | new (`rag_reference`?) |
| Org / Skills mapping + enforcement / gate | new. Becomes explicit `org_ids[]`; the UI currently parses free text |
| Stop / review condition | new |
| Owner / version | new |
| Status | status (BAM: the `misc` trailer) |
| Effective / expiry | new |
| Skill ref | new. Becomes `skill_refs[]` |
| *(editor)* Applies to, FDE usage, Client representative usage, Acts on | new |
| *(editor)* System prompt, Micro prompts [{name, text}] | new. The UI also ships example prompts for Rules-2/3/6/9/12/30 |

BAM fields with no UI column (all manufacturing-specific): `pcb_panelization`, `components_bom`, `materials_finish`, `box_build`, `machine_hr_rates`, `outsourced_services`, `assembly_routing`, `rules_dfm_sourcing`, `rag_reference`.

### 3.3 Agents (UI has 8 columns; BAM 0.2 has 11 fields)

| UI header | BAM 0.2 key |
|---|---|
| Agent ID | stable ID (BAM: the `AG-x` prefix of `description`). The UI adds `AS-nn` for assistants |
| Agent Name | `agent_name` |
| Kind | new: Agent / Assistant |
| Type | new: Internal / External / Proposed |
| Reference | `repo`. ⚠ ADR 0028 reads the Agents `repo` column as a code source, so it must keep this meaning |
| Source | new (for example "Imported · GitHub") |
| Tools | `mcp_server` / `api` / `script`? (shown as chips) |
| Model | `model` |

BAM fields with no UI column: `description`, `api`, `mcp_server`, `script`, `prompt`, `eval`, `logs`. The UI's own tooltip text still names "models, repos, prompts, evals and logs".

### 3.4 Skills (UI has 10 ISATVON columns; BAM 0.2 has 11 fields)

| UI header | BAM 0.2 key |
|---|---|
| Skill ID | stable ID (auto `SK-nn`) |
| Skill | `skill` |
| I · Instruction, S · Source data, A · Automation, T · Tech stack, V · Variables, O · Outcome, N · Next | new. These line up with the FDE-side ISATVON contracts and could reuse their field names |
| References | `documents`? |

BAM fields with no UI column: `rfq_inquiries`, `currency`, `supplier_master`, `commodity_pricing`, `shipping_logistics`, `templates`, `hitl_governance`, `rag_reference`.

### 3.5 Tools (UI has 11 columns; BAM 0.2 TOOLING has 15 fields)

| UI header | BAM 0.2 key |
|---|---|
| Tooling ID | stable ID (`TOOL-<AREA>-n`) |
| Tool Name | `examples`? |
| Category | `category` |
| Links / Path | `links_path` (ADR 0028 reads this as a code source) |
| Owner / access | `owner_access` |
| Status | `record_status` |
| Source version / checked_at | `source_version_checked_at` |
| Refresh Frequency | `refresh_frequency` |
| Record type | `entity_node_type`? |
| Source tab (hidden) | new |
| BuLLM badge (hidden; drives the seal) | new |

BAM fields with no UI column: `search_tag`, `tenant_account_scope`, `security_access_level`, `parent_entity`, `source_evidence_ref`, `binding_review_state`, `search_aliases_category`.

## 4. UI behaviour → how BAM keeps its guarantees

| UI behaviour | In BAM |
|---|---|
| Inline cell edit | `POST /admin/masters/{type}/{id}/versions` with If-Match. A 409 shows "changed by someone else, reload". History is kept silently |
| Delete row / Delete selected | Status → RETIRED and the row is hidden. Never a hard delete, because the audit trail and project snapshots point at versions |
| Add N rows; auto IDs `SK-nn`, `AG-nn` / `AS-nn`, `RULE-CUSTOM-nnn` | The server assigns the next ID when `stable_id` is blank, inside the insert transaction |
| Add files with tables (csv / xlsx / zip / folder) | The client parses and maps as the UI does now, then sends JSON rows to a new `POST /admin/masters/{type}/bulk` (`validate-only` / `upsert`). Server-side row validation and dedupe: exact repeats are skipped, and a changed value on an existing ID is reported and **not** overwritten |
| Add files with documents (pdf, dwg, eml, docx…) | The existing `POST /admin/master-documents` (ClamAV → parse → route) |
| Exact-layout .xlsx workbook | The existing strict importer, regenerated for 0.3 |
| Skills "Import" | Wired to the same bulk flow |
| Add templates lists | A new `master_lists` table (tenant, key, headers JSONB, rows JSONB, row_version), with `GET` and `PUT /admin/master-lists/{key}` (PUT replaces, If-Match) and a CSV download |
| CSV export | Client-side, from the loaded rows (simplest); `?format=csv` on export only if it is needed |
| Search history, facets, theme | Client-side; `localStorage` stays acceptable for per-viewer conveniences |

## 5. Alerts

1. **The schema is the big change.** BAM's frozen BAM 0.2 columns are built into:
   - the contracts (`masters.py`, `masters_workbook.py`, `schemas/masters/*`);
   - the importer, exporter and template;
   - the client masters screen and its tests;
   - `client/e2e/admin-masters.spec.ts`.

   Knowledge snapshots store payloads generically, and ADR 0028 reads the Agents `repo` and Tooling `links_path` columns.
2. **Concepts with no BAM backend yet:**
   - per-org rules with prompts and usage notes;
   - the Agent vs Assistant kind;
   - Tool Factory categories and the badge;
   - the template lists;
   - JSON agent import.
3. **Onyx.** A token is stored in the browser, and client documents go to a third-party server without passing ClamAV or StorageBackend. That breaks the golden rules "secrets from env only" and "files only through StorageBackend". **Removed in the port.**
4. **Wording.** The UI says "FDE workspace" and "editable for FDE", but BAM masters are **admin-only** (`sbam-admin`). The UI labels get renamed.
5. **Dead or unfinished controls:**
   - the Skills Import button;
   - the SharePoint and Drive cards;
   - Onyx's parsed content is never pulled back.
6. **TD §9.2 screens still missing on both sides:** Admin Dashboard, Audit (the API exists), and the Tenants create form (the API exists).

## 6. Issues

| # | Title | Lane / owner review | Blocked by |
|---|---|---|---|
| #780 | `[MUI-1] Contracts: Masters 0.3 columns, Rules/Agents extras, master_lists, bulk import models` | D3 (contracts) | — |
| #781 | `[MUI-2] Backend: 0.2→0.3 data migration, importer/exporter/template 0.3, seed of the approved workbook` | D1/D2 | #780 |
| #782 | `[MUI-3] Backend: bulk rows endpoint, server-assigned IDs, RETIRED-as-delete, master_lists router` | D1 | #780 |
| #783 | `[MUI-4] Client: port the masters grid (Organisations, Skills, Tool Factory + Agents/Assistants) on the BAM API` | D1 | #781, #782 |
| #784 | `[MUI-5] Client: Rules Engine, Add files flow, Add templates, Import agents` | D1 | #783 |
| #785 | `[MUI-6] Client: admin shell and login restyle to the approved look` | D1 (hot: layout, navigation) | — |
| #786 | `[MUI-7] Spike: is Onyx needed next to Cognee? If yes, a server-side connector` | D2 | — |
