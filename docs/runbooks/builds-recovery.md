# A Sub-Skill ended in BUILD_ERROR

Issue WG-38 (#925). BUILD_ERROR is a platform defect (the build could not be made, no correction can fix it), for example
`ProvenanceMissing` for `runtime image digest`. It used to be a dead end: Retry answered 409 `NOT_IN_REVIEW`, Rebuild 409.

## Prevent it: load the skill-runtime registry first

A fresh stack has no skill-runtime registry. Every build pins its image digest in the first stage, `build_manifest`.

- `GET /health/ready` shows `prerequisites.runtime_registry`: `ok`, `missing` or `unknown`. It never changes `status`: the stack is
  healthy, it only cannot build yet.
- `POST W/generate` and `POST W/rebuild` answer **409 CONFLICT**, title "Skill runtime registry is not loaded", with the fix in
  `detail`, before anything is signalled or any Sub-Skill moves.
- Fix: `make runtime-registry` (builds `skill-runtime:local` and syncs its registry), or `make runtime-registry-load` for an image that is
  already built. Both are safe to repeat. Then start the build again.

## Recover: rebuild, do not recreate the project

After the cause is fixed, the FDE presses Rebuild (`POST W/rebuild`, optionally with `sub_skill_ids`). A BUILD_ERROR Sub-Skill is now
accepted: it takes the FDE event `recover` (BUILD_ERROR to BUILDING, audit row `recover` with the actor and both states), and
ProjectPipeline starts its next build number. The platform team's own `retry` from BUILD_ERROR is unchanged.

- Needs the Workflow in NEEDS_FDE_REVIEW and the pipeline at WAIT_REBUILD, like any rebuild. A repeated `Idempotency-Key` recovers once.
- Nothing paid runs twice that was stored: the new build starts from the code attempt the failed build stored (the bodies are copied to
  the new build number, no model call) when the skeleton template is the same, and the stored test dataset is reused as before.
  The review pass is keyed by build number and runs again once; it is the cheapest call.
