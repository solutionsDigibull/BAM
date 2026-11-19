# 0039 — Commit starts the Staging container first and registers it in Bifrost second

- Status: Accepted
- Date: 2026-11-20
- Related: epic #748, issue #909 (WG-32), #766 (Commit), #765 (Staging runtime), #908 (WG-31), refines [0038](0038-staging-compose-restricted-docker-access.md) (its last consequence), [0036](0036-staging-runtime-runs-on-the-deployers-docker-host.md), [0035](0035-commit-to-staging-ports-and-the-132-gap.md), [0032](0032-staging-replaces-the-pre-commit-preview.md), [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)

## Context

Commit (#766) ran `freeze -> register -> start_staging`. `register` asks Bifrost to connect to
`http://<registry name>:8000/mcp` and then checks that the server lists the manifest's tools. On a live stack
(#908) nothing answers at that address: the container did not exist yet, it is later named
`stg-<workflow id>-<registry name>` on the internal `sbam-staging` network, and Bifrost was not on that network.
Bifrost answered HTTP 200 with `"tools": []`, `register` refused ("the server does not expose the manifest's
tools") and Commit never reached Staging. The tests missed it because their fake Bifrost exposed the tools.

## Decision

1. **Start first, register second** (option 1 of #909). `WorkflowCommit` runs `recheck -> freeze -> start_staging
   -> register`. The runtime no longer needs a registry row: `commit_start_staging` builds its request from the
   frozen manifest (`workflow_registry.service.plan`, the same checks `register` makes: FROZEN, WORKFLOW certificate,
   built server, UI bundle certified) and the runtime refuses a request that differs from it (`DIGEST_MISMATCH`). A
   STAGING entry of that version, when one already exists (a re-run), must agree and be REGISTERED.
2. **Bifrost gets the container's real address.** `commit_register` reads the running deployment of this version
   from the audit records (`staging_deployment_started.endpoint`) and registers `endpoint + /mcp`. With no running
   container (`PENDING_RUNTIME`, the default `make up`) it keeps the old default address, as before. The Bifrost tool
   check is unchanged and stays meaningful: a server that does not list the manifest's tools is still refused.
3. **Readiness.** The runtime waits (default 60 s, `STAGING_READY_TIMEOUT_SECONDS`) until the started server answers on
   its address before it records the start, so Bifrost can list the tools right after. A server that never answers is
   stopped and refused (`SERVER_NOT_READY`, recorded FAILED). To probe it, `worker-certify` joins `sbam-staging` in the
   `staging-runtime` profile.
4. **A refused registration stops the container.** When the registry refuses a server that Commit started (missing
   tools, a digest conflict), `commit_register` stops that container and records the deployment FAILED (ADR 0036:
   a failed start is recorded and Commit can run again); the freeze stays (a frozen version never changes), the Bifrost
   entry is left disabled, and Commit ends with the reason. Staging then shows FAILED, not a server the registry does
   not stand behind. Running Commit again restarts it (the same container name is replaced) and registers again.
5. **Bifrost joins `sbam-staging`, in the `staging-runtime` profile only** (`compose/compose.staging-runtime.yaml`).
   `make up` without the profile is unchanged and healthy. The network stays internal; no secret and no raw
   `docker.sock` are added anywhere.
6. **Replay.** The new order sits behind `workflow.patched("909-start-staging-before-register")`. A history recorded
   before it replays in the old order (`workflow_commit_before_909.json`); the new order has its own recorded history
   (`workflow_commit.json`). The activities are the same four, and each is idempotent: freeze (no-op when FROZEN), start
   (an already running container is answered as running, so a Commit that failed after the start and before the
   register starts no second container), register (the same digest is a no-op).

## Consequences

- A real Commit on the `staging-runtime` stack ends in Staging RUNNING with the registry entry's address being the one
  the container serves (WG32-T01/T02, `scripts/staging-proof.sh`). The proof no longer carries a stand-in registry;
  its Workflow image and the WORKFLOW certificate row stay test-only until #130 (PR #902) merges.
- Starting a new version stops the previous container (ADR 0036) before the new version is registered. If the new
  version is then refused, no Staging container runs until Commit is run again; the older registry entry stays
  REGISTERED. Accepted: Staging "lives until the next version" and the refusal says why.
- A Bifrost entry that was created and disabled by a refusal is not re-enabled by a re-run (the registry port has no
  enable call). A refusal means the image really lacks the tools, so a re-run of the same image refuses again; a fixed
  image is a new version with a new entry.
- Bifrost is reachable from Staging containers on the shared internal network; its management API is locked by the
  setup token and the containers' only way out remains the egress proxy.
