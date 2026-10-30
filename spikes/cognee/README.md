# Spike E0.9 (#26): Cognee + OKF bundle

Throwaway prototype backing `docs/spikes/cognee-okf.md` and the ADR. Not product code and not part of
`compose/compose.yaml` (that file belongs to #39). Synthetic data only.

## Layout

| Path | Purpose |
|---|---|
| `docker-compose.yml` | `bifrost` (gateway to OpenRouter) + `runner` (Cognee 1.6.2) on an `internal` network, so the runner has no route to the internet |
| `Dockerfile` | Python 3.12 + pinned Cognee for the runner |
| `bifrost/config.json` | OpenRouter provider; key read from env, never written in the file |
| `src/` | fixture generator, build/test scripts, OKF exporter |
| `fixtures/` | generated synthetic documents for projects A and B |
| `out/okf-bundle/` | prototype bundle for project A (committed) |

## Create `.env` (gitignored; never commit it or paste keys anywhere)

Create `spikes/cognee/.env` with:

```dotenv
OPENROUTER_API_KEY=<your key>          # only Bifrost receives this
BIFROST_ENCRYPTION_KEY=<32 random chars>
LLM_API_KEY=.
EMBEDDING_API_KEY=.
LLM_MODEL=openai/openrouter/openai/gpt-4o-mini
EMBEDDING_MODEL=openrouter/openai/text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
EMBEDDING_MAX_TOKENS=8191
```

Model names live here, not in code, and may need adjusting once the first run shows which
prefixes Cognee/Bifrost accept. The report records the final values.

## Run

```bash
cd spikes/cognee
docker compose up -d --build
docker compose exec runner python -m spike.gen_fixtures   # synthetic documents for projects A and B
docker compose exec runner python -m spike.masters        # master context from the workbook (mock snapshot source)
docker compose exec runner python -m spike.build          # ingest A and B through Bifrost, write okf-bundle/
docker compose exec runner python -m spike.tests          # T01, T01b, T03, T04, T05 -> out/results.json
docker compose down            # keep volumes
docker compose down -v         # wipe Cognee/Bifrost state
```

`spike.tests` creates two Cognee users, so run `spike.build` (which resets the spike stores) before each rerun.
T06 evidence is the compose network design plus the checks in the report. T07: start `spike.t07` in the runner and run
`docker compose stop bifrost` about 8 seconds in; restart Bifrost afterwards.

## Run on a real Admin snapshot (#203, E0.18)

The first run used a mock built from the repo workbook. To re-run on what the Admin masters API produces:

1. Load the tenant's masters through the **Admin portal** (Masters, Import) or `POST /api/v1/admin/masters/import`, then download them with **Masters, Export** (or `GET /api/v1/admin/masters/export` with an Admin token). The export is a workbook in the BAM 0.2 layout.
2. Save it as `docs/reference/real-admin-export.xlsx` (the runner mounts that folder read-only). The path is gitignored, so a real tenant's data cannot be committed by accident.
3. Diff it against the mock (no Docker, no keys needed): `cd spikes/cognee/src && python -m spike.diff_masters "../../../docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx" ../../../docs/reference/real-admin-export.xlsx`. It lists the tabs, columns, entries, statuses and payload cells that differ and exits 1 when there are any. Paste the output into the report (E0.18-T02).
4. Re-run the spike on the export: `docker compose exec runner python -m spike.masters --workbook "/spike/reference/real-admin-export.xlsx"`, then `python -m spike.build` and `python -m spike.tests` (needs the `.env` above with your OpenRouter key).
5. Record the results in `docs/spikes/cognee-okf.md` (E0.18-T01: every check passes, or each failure is explained) and update ADR 0001 only if the Cognee configuration changed.

Bifrost listens on `http://localhost:18080` (host port 18080, because 8080 is often in use).
