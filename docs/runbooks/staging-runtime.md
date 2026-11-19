# Staging runtime on the local stack

Issue WG-31 (#908); ADR [0038](../adr/0038-staging-compose-restricted-docker-access.md) (this setup),
[0036](../adr/0036-staging-runtime-runs-on-the-deployers-docker-host.md) (the runtime),
[0032](../adr/0032-staging-replaces-the-pre-commit-preview.md) (what Staging is);
sibling runbooks: `workflow-registry.md`, `sandbox-host.md`.

After the FDE commits a workflow, SBAM starts the workflow's container by its certified image digest and the workbench
shows its generated UI (the orange Staging toggle). This runbook turns that on for a laptop or a test host, proves
it, and lists what goes wrong.

## What you get

`make up` alone is unchanged: `STAGING_RUNTIME` is `none`, Commit answers `PENDING_RUNTIME`, nothing here runs.
`make up PROFILES=staging-runtime` adds (file `compose/compose.staging-runtime.yaml`):

| Service / network | What it is |
|---|---|
| `docker-socket-proxy` | The only holder of `/var/run/docker.sock`. A filtering HAProxy (`compose/docker-proxy/haproxy.cfg.template`) that allows just the deployer's and the image builder's calls. Internal network `sbam_docker`. |
| `docker-cli-init` | One-shot: copies a pinned static `docker` client into the volume `docker-cli`, which the two workers put on `PATH`, and hands the `staging-egress` volume to uid 10001. |
| `staging-egress-proxy` | The allowlist proxy of ADR 0036 (`sbam_workers.staging.egress_proxy`, image `sbam-egress-proxy:local`). On `sbam-staging` and `sbam_edge`. |
| `sbam-staging` (network) | Internal (no route out). Staging containers, the egress proxy and `sbam-api` are on it. Name from `STAGING_NETWORK`. |
| `worker-certify` | `STAGING_RUNTIME=docker`, `DOCKER_HOST=tcp://docker-socket-proxy:2375`, the egress settings, `SECRETS_KEYS`, `BIFROST_SETUP_TOKEN`. Commit runs here. |
| `worker-build` | Same Docker access for the image builder. `IMAGE_BUILDER` stays `none` unless you opt in. |
| `bifrost` | Gets `BIFROST_SETUP_TOKEN`, which locks its management API (the workers send it to register). Model calls still work with it set (checked with the mock upstream). |

No worker mounts `docker.sock`. A lint test keeps it that way (WG31-T05).

## Steps

1. **Set the names in `compose/.env`** (copy of `compose/.env.example`; never commit it):
   `BIFROST_SETUP_TOKEN` (a long random value: `openssl rand -hex 24`), keep `LLM_PROVIDER=mock`.
   The other names have safe defaults, see the table below.
2. **Bring the database to head, then the stack up:**
   `docker compose --env-file compose/.env -f compose/compose.yaml -f compose/compose.override.yaml run --rm api-migrate`
   (or let `make up` do it) and `make up PROFILES=staging-runtime`. Wait for `make ps` to show every service healthy.
3. **Get the workflow image onto the daemon the proxy reaches** (it is the machine's own daemon, `docker image ls`):
   - built there by hand (`docker build ...`) or loaded (`docker load -i image.tar`), or
   - built by the stack: set `IMAGE_BUILDER=docker` and `STAGING_PROXY_ALLOW_BUILD=1` in `compose/.env`, restart
     `docker-socket-proxy` and `worker-build`. Build steps run on the host daemon and can reach the internet, so this is
     an opt-in. The deployer itself never builds and never pulls: a digest the daemon does not hold is `IMAGE_MISSING`.
4. **Commit a workflow** in the workbench. Staging answers `PENDING_RUNTIME` until the container is up, then `RUNNING`.
   `GET /api/v1/projects/{p}/workflows/{w}/staging` shows the state, health, egress hosts and history.
5. **Run the proof** (mock LLM only, no model money): `scripts/staging-proof.sh | tee proof-output.txt`.
   Use `PROOF_KEEP=1` to leave its Staging containers running afterwards. It is also the integration test
   `server/tests/integration/test_wg31_staging_stack.py` (`make test-int` runs it when the profile is up).

## What the proof does and checks

`scripts/staging-proof.sh` refuses to run unless `LLM_PROVIDER=mock`. It runs `alembic upgrade head` and confirms the
revision is the head (WG31-T01a), builds two tiny test-only images on the daemon, then runs
`server/tests/staging_proof/driver.py` inside `worker-certify`:

| Check | What it proves |
|---|---|
| WG31-T02 | The real runtime (`DeployerStagingRuntime` + `DockerCliDeployer`) started the digest through the socket proxy; `GET .../staging` is `RUNNING` and `HEALTHY` with the egress allowlist enforced. |
| WG31-T02c..d | `GET .../staging/ui/index.html` and `app.js` serve the certified bundle to the assigned FDE with the strict CSP. |
| WG31-T02e, T02g | Another FDE of the tenant (unassigned) and an FDE of another tenant get 404 on the status, the UI and `/mcp`. |
| WG31-T02f | `POST .../staging/mcp` forwards a JSON-RPC tool call to the running container. |
| WG31-T03, T03b, T03c | From inside the container an undeclared host is refused (403) by the egress proxy and logged (`sbam.staging.egress`, in `docker compose logs staging-egress-proxy`); a declared host passes the policy. |
| WG31-T04 | Committing a newer version starts it, stops the old container (host check T04c) and records the old one `SUPERSEDED`. |
| WG31-T05, T05b | No worker holds `docker.sock`; the socket proxy refuses `docker ps`, `exec`, a privileged create and a bind mount. |

**What is faked, and only in the test driver (never in production code):** the WORKFLOW certificate row, because the
producer (#130) is not merged (PR #902 is open): when it is, replace the seeding with the real path; a stand-in Bifrost
registry (see "Known gap"); and the Workflow server, a stdlib script in a `python:3.12-slim` image, not a generated one.
Everything else is real: Keycloak logins, the API and its auth chain, the database, the storage volume, the runtime, the
Docker daemon, the internal network and the egress proxy.

## Environment names

| Name | Default | Meaning |
|---|---|---|
| `STAGING_RUNTIME` | `none` (profile sets `docker`) | `none`: Commit answers `PENDING_RUNTIME`. |
| `STAGING_NETWORK` | `sbam-staging` | The internal network the containers join; the proxy refuses a create on any other. |
| `STAGING_DOCKER_TIMEOUT_SECONDS` | `120` | One `docker` call (10..900). |
| `STAGING_EGRESS_PROXY_URL`, `STAGING_EGRESS_POLICY_DIR` | set by the profile | The proxy the containers use and the volume that holds each deployment's allowlist. |
| `DOCKER_HOST` | set by the profile | `tcp://docker-socket-proxy:2375`; never a socket path in a worker. |
| `SECRETS_KEYS` | the `.env` dev key | Opens a project's stored secrets at a start (ADR 0030). Deployed: a Docker secret file. |
| `BIFROST_SETUP_TOKEN` | empty | Bifrost management token (workers register with it). A secret: `.env` or the environment only. |
| `IMAGE_BUILDER`, `IMAGE_BUILD_TIMEOUT_SECONDS`, `STAGING_PROXY_ALLOW_BUILD` | `none`, `900`, `0` | The image build opt-in (step 3). |
| `STAGING_DOCKER_SOCKET` | `/var/run/docker.sock` | The socket the proxy mounts (only the proxy). |

## What the socket proxy allows

Exactly: `GET _ping|version`, `GET images/<ref>/json`, `GET networks/<name>`, `POST networks/create` (only
`"Internal": true`), `POST containers/create` (a `stg-*` name on `STAGING_NETWORK`, no `Privileged`, `Binds`, `Mounts`,
`Devices`, `CapAdd`, `Sysctls`, host `PidMode`/`IpcMode`/`UsernsMode`, `unconfined`, `label=disable`),
`POST containers/<stg-name|id>/start|stop|wait`, `GET containers/<stg-name|id>/json|logs`, `DELETE containers/stg-*`,
and `POST build` only with `STAGING_PROXY_ALLOW_BUILD=1` (no remote context, no host networking). Everything else is
403. The create body is read with regular expressions: a guard rail against a wrong or injected call, not a sandbox
against a fully compromised worker (ADR 0038, decision 2). Use this on a laptop or a Staging host, not next to
production data.

## Symptoms and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Staging stays `PENDING_RUNTIME` | The profile is not loaded, or `STAGING_RUNTIME` is `none` | `make up PROFILES=staging-runtime`; check `docker compose exec worker-certify printenv STAGING_RUNTIME` |
| `FAILED: IMAGE_MISSING` | The daemon does not hold the certified digest | Step 3. `docker image inspect <digest>` on the host must succeed; the deployer never builds or pulls |
| `FAILED: DOCKER_MISSING` | The docker client volume is not mounted | `docker compose up -d docker-cli-init worker-certify`; `docker compose exec worker-certify which docker` |
| `FAILED: DAEMON_UNREACHABLE` or a 403 in the log | The socket proxy is down, or the call is not on its allowlist | `docker compose logs docker-socket-proxy` shows the refused request line |
| `FAILED: ... NetworkMode` / create refused | `STAGING_NETWORK` of the worker differs from the proxy's | One value in `.env` for both; recreate both |
| `FAILED: DIGEST_MISMATCH` | The registry entry, the manifest and the request disagree (a rebuilt image) | Commit again from the certified version |
| `RUNNING` but health `UNREACHABLE` | `sbam-api` is not on `sbam-staging`, or the server is not listening on 8000 | `docker network inspect sbam-staging`; `docker logs stg-...` |
| A call out of the container gets 403 `no egress policy for this caller` | The proxy cannot read the allowlist: the `staging-egress` volume is not shared, or the token has no password | Both services mount `staging-egress`; the runtime puts `token:x@` in `HTTP_PROXY` (urllib and requests send no credentials for a bare user name) |
| A declared host gets 502 | It passed the policy but the proxy has no route (offline laptop) | Expected offline; the policy decision is the 403 vs not 403 |
| Commit stops at `register` | See "Known gap" | |
| Registration answers 503 "refused the setup token" | `BIFROST_SETUP_TOKEN` of the worker differs from Bifrost's, or is empty | One value in `.env`; recreate `bifrost` and the workers |
| `docker ps` / `exec` in a worker answers 403 | By design | Use the host's Docker, not the worker's |

## Found by the proof

- The egress proxy URL needed a password part (`token:x@`): urllib and requests send no `Proxy-Authorization` for a bare user name (fixed in part 1).
- Next.js redirects the Staging UI frame address `/api/staging-ui/<p>/<w>/` to the same address without the slash, so the bundle's relative `./app.js` resolved beside the workflow folder and the host script (which read the ids from a trailing slash) did not run. The entry page now gets a `<base href>` and the host script accepts both forms (`client/src/lib/staging-ui.ts`).
- The workbench lists Workflows through a contract that requires `created_by`; a hand-seeded row without it made `GET /workflows` answer 500 (the proof seeds `SYSTEM`).

## Known gap (follow-up issue #909)

A real Commit registers the workflow in Bifrost **before** its container exists, and at the address
`http://<registry name>:8000/mcp`, which is neither reachable then nor the container's name (`stg-<id>-<registry name>`).
Bifrost accepts such a registration but lists no tools (measured on the stack: HTTP 200, `"tools": []`), so
`workflow_registry.service.register` ends with "the server does not expose the manifest's tools" and disables the entry.
On a live stack Commit therefore stops at `register` and never reaches Staging, until the order or the address is fixed
(start first, register with the container's address, or do not register Staging entries in Bifrost; #909). The proof drives
the runtime from the committed state with a stand-in registry for exactly this reason. This is a design change to
Commit (ADR 0035/0036), not part of the wiring in #908.

## Stopping and cleaning up

`make down` stops the stack but not the Staging containers: they are plain containers on the daemon, kept on purpose
(`--restart unless-stopped`, stopped only by a newer version). List them with `docker ps -a --filter label=sbam.staging=1`
and remove one with `docker rm -f <name>`. `make reset CONFIRM=yes` removes the volumes `docker-cli` and `staging-egress`.

## Not covered

A deployed staging VM (its own Docker host decision and Docker secret files for `SECRETS_KEYS` and
`BIFROST_SETUP_TOKEN`), a CubeSandbox host (#762), and a policy-aware gateway instead of the HAProxy filter.
