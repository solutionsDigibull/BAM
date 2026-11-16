# Admin Masters UI: in-depth test report

- **Date:** 2026-11-16.
- **What was tested:** `D:\SBAM\Admin_Masters` at commit `40661cd`, the manager-approved UI ([ADR 0029](../adr/0029-approved-masters-ui.md)).
- **How:** a visible Chrome window, slowed down, driven by Playwright. Every run used a fresh browser profile, so the data started from the shipped seed each time.
- **Coverage:** two passes. Pass 1 covered the main paths (about 90 checks). Pass 2 went deep (about 120 checks): edge cases, error states, persistence, keyboard, mobile, security, and the UI team's own test suite.
- **Reliability:** every failure was re-run by hand before being listed. The script mistakes found that way are left out.

## Summary

- The UI is in good shape. The import logic, protection against duplicates and overwrites, CSV parsing, link safety and the XSS defences all held.
- **2 high and 5 medium bugs**, plus a list of minor and UX issues.
- **The UI team's own test scripts are mostly out of date:** 15 of 20 fail because they look for screens and labels that no longer exist.

## Bugs

| # | Sev | Where | Bug | Root cause | Port (#782–#784) |
|---|---|---|---|---|---|
| B1 | **High** | Skills → right-click → Add skill(s) | New skills get IDs that already exist (SK-01, SK-02 …), so IDs are duplicated. Duplicate row numbers correctly (SK-09). | `src/App.tsx:144` `addSkills` uses `/^SK-(\\d+)\\s*$/` (double-escaped inside a regex literal, so it never matches). `nextSkillId` at line 141 is correct. | Removed: the server assigns IDs (#782) |
| B2 | **High** | Organisations, cell editor | **Enter and Shift+Enter close the editor and save**, so a multi-line value (one item per line, as the column help asks) can't be typed. The hint says "Ctrl+Enter to save". | AG Grid handles Enter at grid level. The editor's React `onKeyDown` `stopPropagation` (`OrganizationCellEditor.tsx`) doesn't stop it. Needs `suppressKeyboardEvent` for Enter on these columns. | Must fix in the port (#783) |
| B3 | Medium | Every column's filter popup | **Sort A-Z / Z-A does nothing.** The button shows as pressed, but the row order never changes. Tried on Org ID and Company / Type. | Every column is built with `sortable:false` (`App.tsx:89`), so `applyColumnState` sort is ignored. | Must fix in the port |
| B4 | Medium | Tool Factory (tools, agents, assistants) | The hint says "Ctrl+Enter to save", but **Ctrl+Enter doesn't save** (the edit is lost). Enter and clicking outside do save. | These views use `agLargeTextCellEditor`, where Enter commits. The hint text is shared with Organisations. | Fix the hint or the key in the port |
| B5 | Medium | Skills → Import button | Picks .csv, .xlsx and .json files, **imports nothing and shows no message.** | The input's `onChange` only resets `value` (`App.tsx:160`). | Wire it to the bulk flow (#784) |
| B6 | Medium | Add files, step 1 (Source) | **Text overlaps** ("Choose folder" description over "OR EXTRACT FROM A SOURCE", the info line over the Onyx / SharePoint / Drive cards): 9 overlapping text pairs at 1280×720, 1440×900 and 1920×1080. | The card boxes collapse to about 35 px and their content overflows (CSS in `style.css`, `ExtractFlow` source cards). | Fix the CSS in the port (#784) |
| B7 | Medium | Add files → Onyx | **The API token is stored in plain `localStorage`** (`bullm-onyx-config-v1`), and files go from the browser to a third-party server, skipping the virus scan and storage layer. | `src/onyx.ts` | Removed: Onyx is dropped (#786) |

## Minor and UX issues

| # | Where | Issue |
|---|---|---|
| M1 | Import agents | Importing the same agent twice creates two rows (no duplicate check). Add files does skip duplicates. *(Port: the bulk endpoint deduplicates, #782.)* |
| M2 | Add files → Mapping | A table can show **"Mapped: Yes" but be unticked**, with a summary of "0 ready · 0 need mapping", so Import stays disabled until you notice the checkbox. This happens with aliased headers ("Organisation ID", "Company"). |
| M3 | Add files → Mapping | The README promises per-column mapping dropdowns; only the destination tab can be chosen. Unmatched columns are dropped (with a note). |
| M4 | Rules Engine | Clicking outside the rule editor **throws away edits without a warning.** |
| M5 | Storage full | The error notice shows the raw browser exception ("QuotaExceededError: Failed to execute 'setItem'…"). It's truthful but not readable. |
| M6 | Search | Typo matching ("Rukmni" for "Rukmini") finds nothing, though the README promises typo matches. |
| M7 | Agents / category views | A row added by hand gets a **blank ID**; imported rows get AG-/AS- IDs. A blank row added in a category view lands under "(Uncategorized)", not the category being viewed. |
| M8 | Sample data | All 41 sample rules point at `MFG-SK-*` skills that aren't in the Skills master, so the workflow map shows them under "Other linked skills". *(Port: fix or keep this on purpose in the #781 seed.)* |
| M9 | Accessibility | After Add files or Add templates closes, focus goes to `<body>`, not back to the button that opened it. |
| M10 | Login | Emails are stored as typed (`Admin@Example.COM`), not normalised. |
| M11 | Shell | No favicon: every page load logs one 404. |
| M12 | Dead code | `masterUi.ts` facet filters (`facetFields` / `matchesFacets`) aren't imported anywhere; the README still describes facet menus. |

## The UI team's own suite (`npm run build`, `scripts/*.mjs`)

- **Build:** `npm run build` passes (tsc and bundle). The only warning is a chunk over 500 kB.
- **Pass (5):** `check-bulk-import`, `check-master-ui`, `check-schema`, `check-split`, `check-workbook-migration`.
- **Out of date (15).** They fail because the scripts no longer match the UI, not because the app is broken:
  - **Old labels:** they wait for "Organization Master", "Agent factory" and "+ Add skill" (8 scripts).
  - **Removed tabs:** they expect the Filetype, Rules and Agents tabs and a no-login app (`verify`, `verify-extra`).
  - **Windows path bug:** `check-case-imports` builds `D:\D:\…`.

  They need refreshing before anyone relies on them as a regression check.

## What passed (by screen)

- **Login (20):**
  - validation, Enter to submit, busy state, show/hide password;
  - "Keep me signed in": session vs local storage across tabs;
  - one-time Pipeline banner; the password is never stored;
  - **the production build refuses sign-in and ignores a planted session.**
- **Organisations (34):**
  - all 8 filter match types; filters plus search combine as AND;
  - search suggestions and history; clear button;
  - add 0 / 100 / 101 rows (capped at 100); adding clears the search;
  - Cancel and Save in the editor; click outside saves;
  - stacked comma values with bracket groups kept together;
  - `=1+1` kept as text; a 50,000-character cell saves;
  - links get `noopener`; header tooltips;
  - Rules button disabled with no Org ID;
  - delete Cancel keeps the row; deleting everything stays empty after reload;
  - template downloads in headers.
- **Rules Engine (21):**
  - collapse the map; subskills nest and filter; "Also linked" chips;
  - search covers the usage fields;
  - status pills (Active / Archived / Draft / other);
  - link and unlink skills; the original mapping is kept;
  - prompt labels; empty prompts aren't counted;
  - per-organisation isolation; "Open skill" pre-fills the search;
  - a missing skill shows a notice; the rule ID is locked; edits are saved.
- **Skills (17):** 10 ISATVON columns; Skill ID locked; one click edits; Enter, Tab and Esc behave; chips for tech stack and references; duplicate copies every field; delete; search; saved data.
- **Tool Factory + Import (22):**
  - 14 category toggles, counts equal rows; the search label follows the view;
  - pills, chips and the Imported hover tag;
  - a deleted External agent stays deleted;
  - import from xlsx (header on row 3), csv and json; kind inferred from `AS-` or the Type column;
  - bad, empty and wrongly shaped JSON give errors; Cancel and Esc write nothing; drag-and-drop works.
- **Add files (14):**
  - a multi-sheet workbook from the Masters parent routes each sheet to its tab and leaves out the README sheet;
  - on a single tab, everything maps to that tab only;
  - zip unpacking; a real folder;
  - an unticked file is left out;
  - Back, Cancel and Stop write nothing;
  - re-importing skips duplicates; blank rows are skipped;
  - **a changed value on an existing ID is reported and never overwritten**;
  - a filled template file is held back with a pointer to Add templates.
- **Add templates (12):**
  - BOM, CRLF, quoted commas, multi-line cells and escaped quotes;
  - an empty file or an unbalanced quote gives an error; files over 10 MB are refused; header-only files work;
  - the header-row selector; a wrong-template warning;
  - an upload replaces the list; the 4 downloads have the right columns;
  - **formula-like values download as text cells, not formulas.**
- **Shell and cross-cutting (16):**
  - Tab order; Esc closes every popup;
  - the context menu stays on screen at the edges;
  - dark mode across the dialogs;
  - 320 px and 390 px: no sideways scrolling, and the dialogs and rule editor fit;
  - damaged or wrongly shaped saved data falls back to the seed without crashing;
  - data under the old storage key is picked up;
  - full storage gives an error, not a false save.
- **Security (5):**
  - **XSS:** `<img onerror>` and `<script>` in cells, rule text, imported agent names and template lists render as text. Nothing is injected and no alert fires.
  - **Links:** `javascript:` and `data:` values never become links; only http(s) does.

**Not covered:** SharePoint and Google Drive (disabled in the UI); a real Onyx server; "Switch to Pipeline" (`:5174` isn't running here).
