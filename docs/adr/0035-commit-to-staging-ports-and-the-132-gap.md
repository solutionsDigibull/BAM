# 0035 — Commit to Staging: two ports, and what Commit does while #132 and #765 are not built

- Status: Accepted
- Date: 2026-11-19
- Related: epic #748, issue #766 (WG-18), issues #132 (E14.4, freeze), #765 (WG-17, Staging runtime), #861 (workbench toggle), refines [0032](0032-staging-replaces-the-pre-commit-preview.md) (decision 2), [0030](0030-project-connections-and-secrets.md) (decision 10), [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)

## Context

[0032](0032-staging-replaces-the-pre-commit-preview.md) makes Commit the FDE's final approval: it freezes the
Workflow, registers a Staging entry on the certified digest and starts the container. Two of the pieces it needs
are owned by other issues that are not built: the Workflow-level certify and freeze stages are still stubs in the
pipeline (`certify_workflow`, `freeze_and_version`, #132), and the Staging runtime is #765.

## Decision

1. **`WorkflowCommit` is its own Temporal workflow** (`workflow-commit-<workflow id>`, queue `sbam-certify`), not a
   stage of ProjectPipeline. Four idempotent activities: `commit_recheck`, `commit_freeze`, `commit_register`,
   `commit_start_staging`. A re-run does every step again and each finds what the last run did (freeze is a
   no-op when FROZEN, register is a no-op for the same digest, the runtime answers the running container).
2. **Routes.** `GET W/commit` returns the checks and the secrets gate with the combined manifest hash as `ETag`.
   `POST W/commit` needs `If-Match` (that hash: a stale one is 409 `VERSION_CONFLICT`, none is 428) and an
   `Idempotency-Key`, writes the approval to the audit trail and answers 202 with the run. A failed check or an
   unset or untested secret is 409 with every reason. Only the assigned FDE; everything else is 404.
3. **The recheck reads stored evidence, never a cache**: the Workflow is CERTIFIED or FROZEN with a WORKFLOW
   certificate on the newest manifest hash; every Sub-Skill is certified on the bundle of its newest code attempt;
   the manifest carries the built server; the `UI_BUNDLE` row is PASSED on the manifest's digest; and
   `connections.commit_blockers` is empty. Commit never certifies anything.
4. **Port 1, `WorkflowFreezer`** (the gap in #132). The default `StateMachineFreezer` applies the `freeze` event to a
   CERTIFIED Workflow and seals its newest manifest row. It does not do the ordered Sub-Skill and Macro freeze or
   assign MAJOR.0.PATCH: #132 replaces the adapter behind the same protocol. Because `certify_workflow` is still a
   stub, no Workflow reaches CERTIFIED in the live pipeline, so Commit refuses with `WORKFLOW_CERTIFIED` and a
   plain reason until #132 lands. Commit does not paper over that.
5. **Port 2, `StagingRuntime`** (the Staging runtime, #765). `StagingStartRequest` carries ids, the registered
   digest, the UI bundle digest and secret **names**; the runtime asks `connections.inject.resolve_env` for the
   values itself, so no value passes through Commit. The default adapter answers `PENDING_RUNTIME`, which is
   recorded and shown; a `FAILED` answer ends the run (the commit stays: freeze and registration are kept) and
   running Commit again retries the start. #765 replaces the adapter.
6. **The commit result for the workbench toggle (#861)** is `GET W/commit`: `state` (NOT_COMMITTED, COMMITTING,
   COMMITTED), the STAGING `registration` (digest, registry name, version), the `staging` answer, who approved
   and when, and the newest `run`.
7. **A replaced secret** needs no extra Commit step: a new value makes the connection SET (untested), the gate
   reports `NOT_TESTED`, and Commit is refused until the existing Test connection passes.
8. **No migration.** What Commit records (approval, committed, runtime answer) is audit rows on the `workflow`
   entity; the Staging entry is the `registered_workflows` row of #860.

9. **The UI bundle entry is merged at freeze (#768).** `build_workflow_ui` records the `ui_bundle` entry in the audit
   row `workflow_ui_built`, because the manifest draft has no `mcp_server` to hang it on. The recheck builds the
   effective manifest (stored manifest plus that entry), requires the newest `UI_BUNDLE` row to be PASSED on its
   digest, and re-packages the stored tar: a bundle that is gone or no longer matches the recorded digest is refused
   with 409 (`UI_BUNDLE_CHANGED`) before anything is frozen. `StateMachineFreezer` writes the effective manifest as a
   new frozen row (`build_no + 1`) with a WORKFLOW certificate whose evidence names the hash it derives from, and an
   audit row `WORKFLOW_COMMIT_FROZEN {from_hash, to_hash}`. The FDE approves the hash before the merge; that hash and
   the merged one are the same Commit (the lineage in the audit row), so a re-run after a crash is not "stale".
   The registry then registers the merged manifest's digest and bundle, as #864 requires.
10. **The "start Staging" port for #765** is `StagingRuntime` in `server/api/sbam_api/workflow_commit/ports.py`:
    `start(StagingStartRequest) -> StagingStartResult`. Both models are in
    `server/contracts/sbam_contracts/workflow_commit.py` (`StagingStartRequest` carries ids, the registered image
    digest, the UI bundle digest, secret names and the requester; `StagingStartResult` is `STARTED`,
    `PENDING_RUNTIME` or `FAILED` plus a detail). It is synchronous and idempotent per (workflow, version, digest).
    #765 replaces `default_runtime()`.

## Consequences

- The worker needs `BIFROST_SETUP_TOKEN` (new optional `bifrost_setup_token` setting) to register; without it
  `commit_register` is retried and then fails with the registry's own message. Compose does not pass it yet.
- Follow-ups already in the plan: #132 (real certify and freeze behind `WorkflowFreezer`), #765 (a real
  `StagingRuntime`), #861 (the toggle reads `GET W/commit`).
