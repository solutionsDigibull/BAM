# The Workflow registry in Bifrost

ADR 0028, decision 4; issue WG-12 (#760); spike `docs/spikes/bifrost-mcp.md`.

A certified, frozen Workflow server is registered in Bifrost, and SRIM fetches it from there. SBAM's
table `registered_workflows` is the source of truth; Bifrost can always be rebuilt from it.

## What is registered, and when

- Registering happens only after the Workflow is FROZEN, its manifest is frozen and carries the
  built MCP server, and a WORKFLOW certificate exists on that manifest's hash
  (`workflow_registry.service.register`). Registering again is a no-op.
- The entry is named `wf_<workflow>_v<major>_<patch>` (underscores only: Bifrost forbids hyphens).
- A new version is a new entry. The older one is disabled in Bifrost and marked SUPERSEDED in the
  table. Nothing is deleted (no revocation, C-31).
- SRIM looks a version up through `GET /projects/{project_id}/workflows/{workflow_id}/registration`
  (`?version=1.0.2`; without it, the newest enabled one). It returns the digest that was certified.

## Symptoms and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Registration answers 503 "refused the setup token" | `BIFROST_SETUP_TOKEN` is missing or wrong | Set the secret (below), restart the API and workers |
| Registration answers 503 "did not answer" | Bifrost is down, or answered `database is locked` four times | Check `docker compose ps bifrost`; retry (the call is idempotent) |
| Registration answers 409 "does not expose the manifest's tools" | The image behind the address is not the certified one | Check the image digest of the running container against the manifest; the entry was disabled again |
| An entry is missing in Bifrost after Bifrost lost its database | Bifrost keeps registrations in `/app/data/config.db` | Run `service.reconcile`: it registers every enabled row again |
| A superseded version is still enabled in Bifrost | The disable failed when the new version was registered | Run `service.reconcile`: it disables it |
| An enabled row shows as disabled in Bifrost | Someone disabled it by hand | `reconcile` reports it as `drift`; enable it in the Bifrost dashboard or register a new version |

## Production requirements (spike #750, "Needed before production")

1. **A data volume on `/app/data`** (or Bifrost's Postgres store), so registrations survive a
   recreation. The local stack has none; `reconcile` repairs the loss.
2. **The setup token is a secret.** An environment variable or a Docker secret, never committed,
   never printed. It locks the management API; without it `/api` answers 403. Before wiring it into
   the Compose service, check that model calls through Bifrost still work (not tested yet).
3. **One virtual key per consumer.** SRIM's key is granted only the certified production entries; an
   FDE preview entry is a separate entry that SRIM's key cannot see.
4. **TLS and a network policy** in front of Bifrost, so only SRIM, the workers and the FDE preview
   route reach `/mcp` and the management API.
5. **Retry on `database is locked`**: built into `HttpBifrostRegistry` (four attempts, short backoff).
6. **Our own health check**: `workflow_registry.health.check` asks each registered server for its
   tools over MCP and compares them with the manifest. Bifrost's own `state` lags (spike F11).
7. **Token rotation**: change the secret and restart; the token is read at start-up, never cached
   in the database. Rotate the virtual keys in Bifrost and hand the new one to SRIM.

## Where the server runs

Bifrost registers an **address**, not a container, and cannot start our servers (spike F12). The
default address is `http://<registry name>:8000/mcp` (`default_address`): the generated server
listens on port 8000 over HTTP, and the container is named like its entry. Per ADR 0028 SBAM
registers a launch recipe and SRIM starts the container; pass another `address_for` to `register`
if the server runs elsewhere.
