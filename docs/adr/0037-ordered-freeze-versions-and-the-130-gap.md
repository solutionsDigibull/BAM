# 0037 — The ordered freeze, MAJOR.0.PATCH versions, and what #130 still has to provide

- Status: Accepted
- Date: 2026-11-19
- Related: epic #748, issue #132 (E14.4), issue #130 (E14.2, blocked-by of #132), #129, #127, #766 (Commit), refines [0035](0035-commit-to-staging-ports-and-the-132-gap.md) (decision 4), [0032](0032-staging-replaces-the-pre-commit-preview.md), [0033](0033-ui-bundle-is-part-of-the-certified-workflow.md), [TD §11.4](../design/11-manifest-design.md), [TD §13.4](../design/13-temporal-design.md)

## Context

Commit (#766) calls the `WorkflowFreezer` port. Its interim adapter only sealed the newest manifest and
applied the Workflow `freeze` event. #132 is the real freeze: ordered, versioned, immutable. It is listed
as blocked by #130 (Macro and Workflow validation, the producer of the WORKFLOW certificate), which is
itself blocked by #129 and #127. None of those is built. The owner decided to take #132 in parallel
(#132 keeps its `blocked` label, by design, until #130 closes).

## Decision

1. **`OrderedFreezer`** (`server/api/sbam_api/versioning/freezer.py`) implements `WorkflowFreezer` and
   replaces `StateMachineFreezer` as the worker's default. It extends the interim adapter (which stays, split
   into `eligible`, `seal`, `transition`, as the base and for the Commit tests). Commit's behaviour is
   unchanged: only the object behind the port differs. The order is the one of TD §13.4, all in one
   transaction and idempotent:
   eligibility on stored evidence, read-back of every stored file, Sub-Skills then Macro Skills freeze
   (state-machine events, audited), file index rows and newest Sub-Skill manifests get `is_frozen`,
   the Workflow manifest is sealed with its WORKFLOW certificate, the `workflow_versions` row is
   written, the Workflow `freeze` event is applied, then the manifest hashes are recomputed and the files
   read again. A mismatch is `HashMismatch` (409): the caller rolls back, the version is not frozen and the
   Workflow stays CERTIFIED (the failure is logged at error level for the operator alert).
2. **Eligibility reads evidence, never certifies.** Every Commit check except the secrets gate must pass
   (WORKFLOW certificate on the newest hash, each Sub-Skill certified on its newest bundle, built
   server, UI_BUNDLE evidence from #864). The freeze never writes a certificate of its own except the
   derived one `seal` already wrote for the manifest with the merged bundle entry (#768).
3. **Versions.** `numbers.py`: `MAJOR.0.PATCH`, minor always 0 (`^\d+\.0\.\d+$`). MAJOR is the
   Workflow's own `major` (each approved SOW version is the next one), so the first freeze is
   `MAJOR.0.0` (1.0.0, then 2.0.0 for a new SOW); a freeze after a patch is the highest frozen PATCH of
   that Workflow plus one (1.0.1, 1.0.2). `workflow_versions` has one row per frozen version
   (unique `(workflow_id, version)`), written already frozen. `workflows.version` follows the last freeze.
4. **Superseded is derived.** Frozen rows are never edited. `GET W/versions` marks a version superseded
   when a newer one exists in the project; the frozen Sub-Skills of an older Workflow take the
   `superseded` state-machine event when a newer Workflow freezes. No revocation.
5. **Patch route.** `POST W/sub-skills/{sub_skill_id}/new-version` (FDE, `Idempotency-Key`) opens the next
   PATCH: Sub-Skill `start_patch_version` (back to DRAFT), its Macro Skill and the Workflow to BUILDING.
   Not FROZEN (a CERTIFIED version, or one that is being frozen) is 409 `NOT_FROZEN`. The next
   version number is audited (`WORKFLOW_PATCH_STARTED`) and the freeze reads it for the change reason.
6. **Immutability at the database.** Migration 0062 creates `workflow_versions` (frozen trigger) and adds
   the existing `forbid_frozen_change` trigger to `artifacts`. `sub_skill_manifests`,
   `workflow_manifests`, `certifications` and `sandbox_runs` already had it. The trigger fires for every role
   and raises SQLSTATE `SB423`, which the repositories map to 423. The API lock keeps its existing codes
   (`RESOURCE_LOCKED`, `WORKFLOW_FROZEN`); the issue text names `FROZEN`, and renaming a code other
   routes and the UI already depend on is out of scope here.
7. **Files.** Stored objects are content-addressed and written read-only (`LocalDiskBackend` chmods 0o444;
   S3 objects are immutable by key). The freeze verifies them by re-reading, not by changing permissions.

## What #130 still has to provide for a real end-to-end freeze

- **The WORKFLOW certificate producer** (`certify_workflow`, still a stub in the pipeline). Today a
  Workflow reaches CERTIFIED only in tests; in the live pipeline the Workflow-level certify must write the
  WORKFLOW certificate on the manifest hash and apply `workflow_certified`. Until then Commit refuses with
  `WORKFLOW_CERTIFIED` and the freeze refuses with the same reason.
- **Macro Skill certification.** The Macro Skill machine is not advanced by the pipeline. As an interim,
  once every Sub-Skill under it is certified (checked first), the freeze brings it through
  `build_started` / `all_sub_skills_certified` and freezes it. #130 owns macro validation and should
  advance the machine itself; the freeze then only applies `freeze`.
- **Pipeline stage `freeze_and_version`** stays a stub: Commit, not a stage, triggers the freeze (ADR 0032).

## Not done here (recorded)

- E14.4-T04 (a filesystem tamper test) and the nightly re-verify of all frozen hashes: the storage
  protocol has no ownership or schedule hook; `python -m sbam_api.artifacts.verify` (E16.5) already
  re-hashes frozen artifacts and is the job to schedule.
- The security-log entry for a tamper attempt through the API: rejected writes already answer 423
  and appear in the request log; a dedicated security log is not built.

## Consequences

- One migration (0062, reversible). Contracts are additive (`workflow_versions/*`).
- Commit's Staging unique rule (one Staging deployment per project and version) is the registry's
  (#860); the freeze only supplies the version.
