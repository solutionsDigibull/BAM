# Handoff: Admin Masters UI adoption (MUI) and follow-ups, 2026-11-19

From prateekh-spec (paused). For the teammate who takes over. Spec and decisions:

- [ADR 0029](../adr/0029-approved-masters-ui.md)
- [UI mapping](../research/admin-masters-ui-mapping.md)
- [UI test report](../research/admin-masters-ui-test-report.md)

The approved UI is `D:\SBAM\Admin_Masters` (repo ParvithaCGowda29/Admin_Masters). **BAM adapts to the UI, not the reverse.**

## Status

| Issue | What | State | Branch / PR |
|---|---|---|---|
| #780 MUI-1 | Contracts: Masters 0.3, master lists, bulk models | Merged | PR #792 |
| #781 MUI-2 | 0.2→0.3 migration, importer/exporter, seed | Merged | PR #834 |
| **#782 MUI-3** | Bulk endpoint, server-assigned ids, retire-as-delete, master lists API | **PR open, in review** | `782-masters-bulk-lists`, **PR #880** |
| **#785 MUI-6** | Admin shell + `/login-admin` in the approved look, light/dark | **Draft PR open** (see "Left on #785") | `785-admin-shell-restyle`, draft PR |
| #869 MUI-8 | Admin ↔ Admin Masters toggle, `/login-admin-masters`, repo-style project list | Not started. Full brief below | — |
| #783 MUI-4 | Port masters grids (Organisations, Skills, Tool Factory, Agents/Assistants) on AG Grid + BAM API | Not started | blocked by #782, #869 |
| #784 MUI-5 | Rules Engine, Add files, Add templates, Import agents | Not started | blocked by #783 |
| #786 MUI-7 | Onyx spike | Decision: **not needed for this release**. Doc-only PR with `Closes #786` | — |
| #776 WG-23 | Token budget caps per project | Not started. Check overlap with PR #853 first | — |
| #777 WG-24 | Cognee ends a build on a credit refusal / reached cap | Not started. **Only after PR #853 merges**, or with a minimal diff to the meter callbacks. Simulate the refusal; never call a real model | — |
| #670 A-12b | Auth-state cache invalidation | **Already landed on dev** in PR #875 by someone else. Check whether #670 can close; don't redo it | — |
| #729 | Enterprise Assistant LLM decision | **Already landed on dev** as ADR 0034 (PR #876). Check whether #729 can close; don't redo it | — |

All of these issues are assigned to prateekh-spec. Reassign the ones you take.

## #782 (PR #880): what reviewers should know

- Bulk upsert writes only the clean rows. A changed existing id is a conflict and is never overwritten. This deliberately differs from the workbook import, which adds a version.
- Retire changes only the entry status. On Rules and Tooling, a later re-import recomputes the status from `record_status` and can revive the row.
- Migration `0062_master_lists`. **If another migration lands on dev first, renumber 0062 before merge. Never use `alembic merge`.**
- Verified live on the compose stack: 5 parallel blank creates got 5 distinct ids. CI runs the full security suite; it was too heavy for the laptop.

## Left on #785 (draft PR)

Done:
- The shell restyle, the admin-only theme toggle (`client/src/components/shell/admin-theme.ts`, localStorage `sbam-admin-theme`) and the avatar account menu.
- `BullmLogin` shared by both logins. `dev-login.tsx` is deleted.
- The dark-mode table fix in `ui-kit/grid.tsx`: CSS variables, with the old light values as fallbacks.
- `Session.user` for "Signed in as".
- Screenshots in `client/walkthrough-out/mui6/` (local, untracked).

Still to do before marking it ready:
1. **`next build` was never verified.** Locally it fails only on the untracked `client/e2e/stack/zz-*.spec.ts` throwaway files. Let CI build it, or build in a clean checkout.
2. e2e `e12_6_t06` ("50x23 gates render within 1 s") measured 1309 ms on the laptop. Compare it against a dev baseline. It's probably machine speed.
3. Some screens still hard-code light colours, e.g. `project-form-drawer`'s `#e5e7eb` border. Dark mode only covers antd tokens and the ui-kit variables.
4. FDE pages share PortalShell, so they get the new light look but no theme toggle. That's by design.

## Next: the full brief for #869 (MUI-8)

**Branch.** Create `869-admin-masters-mode` from `785-admin-shell-restyle` as a stacked PR. If #785 has merged by then, branch from `dev` instead.

**Scope**

1. **`/login-admin-masters`.**
   - The same `BullmLogin` with `portal="admin"`, the same Keycloak client and the same `sbam-admin` role.
   - Default `callbackUrl=/admin/masters`. `safeCallback` already allows `/admin/*`.
   - No `proxy.ts` change, because login pages aren't guarded.
2. **Mode toggle.**
   - A new `client/src/components/shell/mode-toggle.tsx`: a segmented "Admin | Admin Masters" control in the admin header.
   - The mode is `pathname.startsWith("/admin/masters")`.
   - Choosing Admin goes to `/admin/projects`; choosing Admin Masters goes to `/admin/masters`.
   - Make it keyboard-accessible.
   - Don't reuse or touch the workbench shell or the FDE/Staging toggle (Harish's lane).
3. **Sidebar per mode, additive in `components/shell/nav.ts`.**
   - Admin mode is `ADMIN_NAV` without the Masters item.
   - Admin Masters mode is `MASTERS_NAV`: Projects (`/admin/masters`) and Tenant masters (`/admin/masters/tenant`).
4. **`/admin/masters` home: a repo-style list of the tenant's projects.**
   - Data: `useList({resource:"admin/projects", pagination:{mode:"off"}})`.
   - Each row: name (link), client, "Updated …", status chip.
   - Search, newest first, and an Archived filter (read-only).
   - A "Tenant masters" card at the top.
   - States: loading, empty (with a link to Admin → Projects), error (problem+json detail) and forbidden.
5. **Masters pages.**
   - `/admin/masters/tenant` renders the existing `MastersScreen` unchanged.
   - `/admin/masters/projects/[projectId]` renders `MastersScreen` with a `projectId` prop. Pass it as `project_id` on the list, create and import calls (`masters-screen.tsx`, `master-table.tsx`, `entry-drawer.tsx`, `import-wizard.tsx`).
   - A non-UUID id gives notFound. A cross-tenant project gives the API's 404, shown as not-found.
   - Masters storage stays tenant-level in this round. Real per-project masters storage is a follow-up.

**Tests**
- Vitest:
  - `mui_8_t01` the toggle follows the path and navigates.
  - `t02` the nav changes per mode.
  - `t03` the project-list states and search.
  - `t04` the project masters page sends `project_id`.
  - `t05` the login default `callbackUrl`.
- Playwright (default project, faked API like `admin-masters.spec.ts`):
  - `t06` a toggle round-trip.
  - `t07` an FDE on `/admin/masters` sees Forbidden and makes zero API calls.
  - `t08` no overflow at 320 and 390 px.
- Move the tab assertions in `client/e2e/admin-masters.spec.ts` to `/admin/masters/tenant`.

**Acceptance criteria**
- `sbam-admin` switches modes without signing in again.
- An FDE gets Forbidden.
- No overflow at 320 and 390 px.

## Then #783 and #784

The scope is in the issues and in section 2 of the [UI mapping](../research/admin-masters-ui-mapping.md).

**Patterns to reuse**
- **AG Grid 36:** `ModuleRegistry.registerModules([AllCommunityModule])`, `theme={themeQuartz}`, loaded with `next/dynamic` and `ssr:false`, the same way as `client/src/app/(fde)/projects/[projectId]/workflows/[workflowId]/isatvon/isatvon-grid.tsx`.
- **Edits:** a cell edit is a new version, `POST .../versions` with `ifMatch()` from `providers/data-provider.ts`.
- **Delete:** call the #782 retire route.
- **Add files, imports and templates:** go through #782 `bulk` and `master-lists`.

**Bugs from the UI test report to fix while porting**
- B2: Enter must not close the multiline editor. Use `suppressKeyboardEvent`.
- B3: sorting does nothing. Set `sortable:true`.
- B4: the Ctrl+Enter hint is wrong.
- B5: the Skills Import button is dead.
- B6: Add files step 1 overlaps.

**Not allowed:** no Onyx (#786), and no data kept only in localStorage.

**Dependencies:** none of fuse.js, xlsx or jszip is a direct client dependency yet. Add them only if needed, as a lockfile change owned by D1.

## Rules that bit us (read before you start)

- **Harish's lane:** don't touch #862, #767, #768, #766, #765, #861 or #867, the workbench shell (`client/src/workbench/**`), the FDE/Staging toggle, `deployToStaging()` or the Commit screens.
- **Next.js stays on 16.4.2** (#840 dropped by the owner). Ignore the "next upgrade" nudge; `next dev` sometimes fails to start and simply needs a retry.
- **Hot files** (`client/src/app/layout.tsx`, `components/shell/**`, `providers/**`): run `gh pr list -R solutionsDigibull/BAM --state open --search "<path>"` before each client PR, and keep the diff additive.
- **PR base:** PRs target `dev`. The `origin` remote here is `solutionsDigibull/BAM` itself.
- **This Windows laptop:**
  - `make` isn't installed; use the raw `docker compose --env-file compose/.env -f compose/compose.yaml -f compose/compose.override.yaml …`.
  - Windows-only test failures: `test_e3_8_t04` (Defender blocks EICAR), the `os.killpg` PDF-parse tests, the bash/WSL Keycloak seed tests, and mypy `resource.RLIMIT_*` in the ingest isolation files.
  - The full security suite (1202 cases) runs out of memory locally. Run suites one at a time.
- **Stack ports here:** web 3000, API 8082, Keycloak 8089.
  - The stack API serves `server/` from this checkout with `--reload`.
  - Apply new migrations with `MSYS_NO_PATHCONV=1 docker exec -w /app/server sbam-sbam-api-1 python -m sbam_api.db.migrate upgrade head`. The stack DB is now at `0062_master_lists` from the #782 branch.
- **Live e2e on the stack:**
  - Set `E2E_SEED_PASSWORD` (= `SEED_USER_PASSWORD` in `compose/.env`), `E2E_BASE_URL=http://localhost:3000` and `E2E_STACK=1`.
  - Run `pnpm exec playwright test <spec> --project=stack` from `client/`.
  - The #782 probe spec is kept outside the repo; re-create it from PR #880's test notes if needed.
- **Untracked local files belong to the user:** `client/e2e/stack/zz-*.spec.ts`, `client/walkthrough-out/`, `.agents/`, `docs/audit/07-*`, `skills-lock.json`. They also break `next build` and the repo-wide lint and typecheck locally. Never commit them.
