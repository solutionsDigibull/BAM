# The Workflow registry in Bifrost

ADR 0028, decision 4; ADR 0032; issues WG-12 (#760) and WG-26 (#860); spike `docs/spikes/bifrost-mcp.md`.

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

## Entry kinds: staging and production (ADR 0032, #860)

Every entry has a `kind`, stored in `registered_workflows.kind` and returned by the lookup.

| Kind | Registered by | Bifrost name | Who can see it |
|---|---|---|---|
| `staging` | Commit (#766) | `wf_<workflow>_v<major>_<patch>_stg` | the assigned FDE only; SRIM's production key cannot |
| `production` | promotion (#148, not built yet) | `wf_<workflow>_v<major>_<patch>` | SRIM's key |

- `service.register` takes `kind` with no default, so a caller must choose. Both kinds carry the digest
  and manifest hash that were certified: a staging entry never rebuilds the image, and promotion
  registers the same digest again as `production`.
- A Workflow version has one entry per kind (unique on tenant, workflow, version, kind). Registering the
  same staging version again is a no-op.
- A newer version supersedes the older entry of the SAME kind. A staging entry never supersedes or
  disables a production one. Older rows stay listed, never deleted.
- The lookup takes `?kind=staging|production`. With no kind the assigned FDE sees both, staging first.
  A production lookup of a staging-only workflow is a 404, like another tenant or an unassigned project.
- Rows that existed before kinds were added became `production` (they were registered for SRIM).
- The workbench registry panel (#849) shows the kind.

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
3. **One virtual key per consumer.** SRIM's key is granted only the certified production entries; a
   staging entry is a separate entry (name ends in `_stg`) that SRIM's key cannot see. Build each grant
   from `service.names_for_key(session, ctx, kind)`: exact names from SBAM's table, never a pattern
   such as `wf_*`, which would match the staging names too.
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
