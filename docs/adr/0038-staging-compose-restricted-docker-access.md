# 0038 — Staging in Compose: a filtered Docker socket proxy, an internal network, and an opt-in profile

- Status: Accepted
- Date: 2026-11-19
- Related: epic #748, issue #908 (WG-31), #765, #766, #861, #867, refines [0036](0036-staging-runtime-runs-on-the-deployers-docker-host.md) (decisions 1 and 4), [0035](0035-commit-to-staging-ports-and-the-132-gap.md), [0030](0030-project-connections-and-secrets.md), [0028](0028-workflow-mcp-server-repo-first-bifrost-registry.md)

## Context

The Staging runtime (#765), Commit (#766) and the workbench Staging mode (#861) are merged, but on a stock stack
nothing sets `STAGING_RUNTIME` and friends, there is no egress proxy service, a worker has no safe way to reach the
Docker daemon that holds the built image, and `BIFROST_SETUP_TOKEN` reaches neither Bifrost nor the workers. Staging
therefore stays `PENDING_RUNTIME`. Mounting `/var/run/docker.sock` into a worker would give a compromised worker root
on the host, which AGENTS.md and the issue forbid.

## Decision

1. **An opt-in profile, `staging-runtime`** (`make up PROFILES=staging-runtime`, file
   `compose/compose.staging-runtime.yaml`). The name avoids a clash with `compose.staging.yaml`, which is the staging
   VM's environment overlay. Without it nothing changes: `STAGING_RUNTIME` defaults to `none` in the worker settings,
   Commit answers `PENDING_RUNTIME` and `make up` is as healthy as before. The profile sets `STAGING_RUNTIME=docker` on
   `worker-certify` (where the Commit activities run), plus `DOCKER_HOST`, `STAGING_NETWORK`,
   `STAGING_EGRESS_PROXY_URL`, `STAGING_EGRESS_POLICY_DIR`, `STAGING_DOCKER_TIMEOUT_SECONDS`, `SECRETS_KEYS` and
   `BIFROST_SETUP_TOKEN` (names only in `compose/.env.example`; values from `.env` or the environment).
2. **A filtered socket proxy is the only holder of the socket.** `docker-socket-proxy` is
   `tecnativa/docker-socket-proxy` pinned by version and digest, with our own HAProxy template
   (`compose/docker-proxy/haproxy.cfg.template`) in place of the stock one. The stock proxy can only allow whole
   sections: `CONTAINERS=1` also allows `containers/<id>/exec` and a create with `Privileged` or host binds, which
   is no restriction at all. Ours allows exactly: `GET _ping|version`, `GET images/<ref>/json`, `GET networks/<name>`,
   `POST networks/create` (only `Internal: true`), `POST containers/create` (only a `stg-*` name on the Staging
   network, and no `Privileged`, `Binds`, `Mounts`, `Devices`, `CapAdd`, `Sysctls`, host `PidMode`, `IpcMode`,
   `UsernsMode`, `unconfined` or `label=disable`), `POST containers/<stg-name|id>/start|stop|wait`,
   `GET containers/<stg-name|id>/json|logs`, `DELETE containers/stg-*`. Everything else is 403: exec, attach, volumes,
   images pull/push/delete, events, swarm, other containers (so a worker cannot read another container's
   environment). The proxy listens on the internal `sbam_docker` network that only `worker-certify` and `worker-build`
   join. Residual risk: the filter reads the create body with regular expressions, so it is a guard rail against a
   mistaken or injected call, not a sandbox against a worker that is fully compromised; Staging runs on a developer or
   staging host, never next to production data. A policy-aware gateway can replace it later behind the same port.
3. **The docker client is not baked into the worker image.** `docker-cli-init` copies one static client (pinned
   `docker:27.5.1-cli` digest, no plugins) into a volume the two workers add to `PATH`. With no `buildx` plugin the
   client uses the legacy builder, which the proxy allows (`POST /build`).
4. **The image reaches the host through the same proxy.** The image builder (#879) and the Staging deployer must talk
   to the same daemon: both use `DOCKER_HOST=tcp://docker-socket-proxy:2375`. Building is an opt-in
   (`IMAGE_BUILDER=docker` and `STAGING_PROXY_ALLOW_BUILD=1`, default `none` and `0`): the proxy refuses `/build`
   unless `BUILD=1`, and a build never gets a remote context, host networking or a custom cgroup parent. Build steps
   run on the host daemon and can reach the internet, which is why it is off by default. Without it, an image that is
   already on the daemon (built there, or loaded with `docker load`) is what Staging starts; the deployer never builds
   and never pulls (0036 decision 2).
5. **The Staging network and the egress proxy are services of the profile.** `sbam_staging` is internal, named by
   `STAGING_NETWORK` (so the deployer's `network inspect` finds it and it never has to create one). `sbam-api` joins it
   to probe and forward to the container. `staging-egress-proxy` runs `sbam_workers.staging.egress_proxy` on the Staging
   network and `sbam_edge` (the only network with a route out); worker-certify writes each deployment's allowlist into the
   shared `staging-egress` volume, the proxy reads it read-only. `docker-cli-init` gives the volume to uid 10001.
6. **Bifrost's setup token.** `BIFROST_SETUP_TOKEN` is passed to `bifrost` (it locks the management API) and to
   `worker-certify` (it registers the entry). It is an environment value the operator sets; no compose file carries it.
   The deployed-VM wiring (a Docker secret file `*_FILE`, as `SECRETS_KEYS_FILE` does) is not part of this profile,
   which `compose.deploy.yaml` and `scripts/compose_lint.py` never load.
7. **A lint test keeps the rule.** WG31-T05: no service whose name starts with `worker-` (or any service other than
   `docker-socket-proxy`) mounts a docker socket, in any compose file or with the profile on.

## Consequences

- `make up PROFILES=staging-runtime` is the way to run a Staging deployment on a laptop; the runbook
  `docs/runbooks/staging-runtime.md` has the steps and the proof (`scripts/staging-proof.sh`).
- A deployed staging VM still needs its own Docker host decision and secret files; not solved here.
- The registry entry's Bifrost address (`http://<registry name>:8000/mcp`) is not the container's Staging address, and
  registration runs before the container exists, so a real Commit stops at `register` on a live stack. Follow-up: #909. See the runbook, "Known gap".
