# 0036 — The Staging runtime runs on the deployer's Docker host; egress goes through an allowlist proxy

- Status: Accepted
- Date: 2026-11-19
- Related: epic #748, issue #765 (WG-17), #766, #861, refines [0032](0032-staging-replaces-the-pre-commit-preview.md) (open points: where containers run, how the bundle is served), [0035](0035-commit-to-staging-ports-and-the-132-gap.md) (decision 10), [0023](0023-sandbox-adapter.md), [0030](0030-project-connections-and-secrets.md)

## Context

[0032](0032-staging-replaces-the-pre-commit-preview.md) left open where Staging containers run (a sandbox VM or the
deployer's Docker host) and how the UI bundle is served inside the FDE shell. #765 builds the runtime behind the
`StagingRuntime` port of [0035](0035-commit-to-staging-ports-and-the-132-gap.md).

## Decision

1. **Containers run on the deployer's Docker host** (the same host and `DOCKER_HOST` the image builder of #867 uses),
   through a `ContainerDeployer` port: `DockerCliDeployer` (docker first), `DisabledDeployer` (default,
   `STAGING_RUNTIME=none`: Commit completes and Staging answers `PENDING_RUNTIME`, so `make up` stays healthy) and a
   fake in tests. The KVM host (#762) is not needed. A sandbox VM can be a later adapter behind the same port.
2. **Start by digest, never rebuild.** The runtime refuses (`DIGEST_MISMATCH`) unless the request, the STAGING registry
   entry and the frozen manifest agree on the image digest, the manifest hash and the UI bundle digest, and the stored
   bundle still matches its digest (`UI_BUNDLE_CHANGED`). The host must already hold the image (`IMAGE_MISSING`
   otherwise): no `docker build`, no `docker pull`.
3. **Secrets at start only.** The runtime calls `connections.inject.resolve_env` with the secret names; values go to the
   `docker run` client's environment (`-e NAME`, never an argument, file, log, audit row or manifest). Names that
   could shadow the client's environment are refused. A worker without `SECRETS_KEYS` refuses to start a workflow
   that needs secrets.
4. **Egress.** Containers join an internal Docker network (no route out). With `STAGING_EGRESS_PROXY_URL` and
   `STAGING_EGRESS_POLICY_DIR` set, `HTTP(S)_PROXY` points at `sbam_workers.staging.egress_proxy`, which allows only the
   hosts of the certified manifest (`ui_bundle.allowed_hosts`), ports 80 and 443, per-deployment token; a blocked
   request is answered 403 and logged (`sbam.staging.egress`). Without a proxy the container has no outside access at
   all (fail closed; `egress_enforced=false` is shown). Compose wiring of the proxy is a D2 follow-up.
5. **One deployment per project and version; no time limit.** A deployment is audit rows on the `workflow` entity
   (`staging_deployment_started|stopped|failed`), no table, no migration. Starting a version stops every other running
   deployment of the Workflow (`docker stop`, container kept) and records it `SUPERSEDED`; history is never deleted.
   Nothing stops a deployment on a timer.
6. **Serving.** Only the assigned FDE (others 404): `GET W/staging` (state, health, history), `GET W/staging/ui/{path}`
   (files of the certified bundle, digest-checked, strict CSP with `connect-src 'self'` plus the declared hosts) and
   `POST|DELETE W/staging/mcp` (SBAM forwards to the recorded container address; the FDE token is not forwarded). The
   UI is fetched with the bearer token (not an iframe `src`); the shell mounts it and sets the workflow server base to
   `.../staging/mcp`'s parent.

## Consequences

- Worker settings: `STAGING_RUNTIME`, `STAGING_DOCKER_TIMEOUT_SECONDS`, `STAGING_NETWORK`, `STAGING_EGRESS_PROXY_URL`,
  `STAGING_EGRESS_POLICY_DIR`, `SECRETS_KEYS`. Compose does not set them yet.
- `GET W/commit` now also shows the `staging` answer for a bundle-merged manifest (the lineage hash was not matched).
