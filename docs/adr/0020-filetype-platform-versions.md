# 0020 — Filetype rows are platform data; a super-admin changes them with a workbook version

- Status: Accepted
- Date: 2026-10-27
- Related: issues #257 (E3.13), #254, #56 (E3.3); refines [0006](0006-masters-and-admin-context.md) (O-23); [TD §7](../design/07-database-design.md), [TD §8](../design/08-api-design.md)

## Context

The 43 Filetype rows (BAM 0.2's frozen tab) could only ever be the shipped table. Every masters
import refused a different Filetype tab with 403 `FILETYPE_FROZEN` and never checked the role. A
super-admin has no tenant, so `/admin/masters/*` answered 404 for them. The default for O-23 in
ADR 0006 says only a super-admin may change the table, through a new workbook version. It doesn't
say whether the change is per tenant or for the whole platform, or how a super-admin reaches it.

## Decision

- **Platform-wide.** There is one set of Filetype rows for the platform, like `tenants`. The rows
  live in `filetype_row_versions` (row label, version, payload, workbook sha256, version label,
  created by/at), which has no tenant column. The table is append-only through the same trigger
  as the masters version tables. Nothing is seeded: a row with no version is the shipped row
  (`masters_filetype.json`), so the first change is version 2.
- **A separate endpoint, for super-admins only:** `POST /admin/filetypes/import`.
  - It runs the same file checks as the masters import (size, ClamAV, container, BAM 0.2
    structure, so still 43 rows labelled Filetype #1..#43) and uses only the Filetype tab.
  - `validate-only` reports. `upsert` writes one version per changed row in one transaction.
  - An unchanged tab, or the same workbook imported twice, writes nothing.
  - The optional `version_label` form field records the owner's sign-off, e.g. "BAM 0.3, approved by ...".
- **Tenant admins** keep `/admin/masters/import`. Its Filetype tab must equal the *current*
  platform rows, else 403 `FILETYPE_FROZEN` with nothing written.
- **Audit:** one `FILETYPE_VERSION_IMPORTED` row per applied import. It has a NULL tenant (a
  platform event, so it isn't in any tenant's `/admin/audit`) and records the actor, the workbook
  sha256, the version label, the changed labels, and the before and after of each row. #254 called
  this action `FILETYPE_CHANGED`. One action per import keeps a workbook's rows together.
- **Loader behaviour stays config.** `filetypes.yaml` (`mvp_enabled`, loader, extensions, magic,
  `default_route`) is never read from or written by the workbook. Upload allowlisting and type
  detection keep using it and the shipped table. An import that changes the file format of an
  MVP-enabled row is applied, but it logs a warning and records it in the audit. Changing
  detection needs a config change and a release.
- `GET /admin/filetypes` returns the current platform values (the shipped rows overlaid with
  their newest versions), joined with the loader config.

## Consequences

- A super-admin changes the reference data every tenant's masters import is checked against, and
  the change shows up in the platform view right away. It doesn't change how documents are
  detected or parsed.
- Per-tenant Filetype rows would need a new decision and a tenant column, which this one leaves out.
