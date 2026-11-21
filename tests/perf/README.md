# Performance tests (E15.4, #138)

k6 scenarios for the release performance targets in PLAN §28 "Release acceptance targets"
(`docs/plan/06-mvp-dod-delivery-release.md`) and issue #138. Every target lives in
[`lib/targets.js`](lib/targets.js) and is encoded in each script's `options.thresholds`, so a breach
makes `k6 run` exit non-zero (99). Nothing here is wired into CI yet.

| ID | Script | What it does | Threshold (fails the run) |
|---|---|---|---|
| T01 | `scenarios/t01-interactive.js` | 10 VUs for 15 min, weighted mix of 11 FDE read endpoints | `http_req_duration{scenario:interactive}` p95 < 500 ms; `http_req_failed` < 1%; 0 × 5xx; checks > 99% |
| T02 | `scenarios/t02-grid.js` | The workbench's two parallel loads (ISATVON schema + `macro-skills` tree), first iteration tagged `cache:cold` | `grid_load_ms` p95 < 2000 ms; `http_req_duration{scenario:grid}` p95 < 2000 ms; tree has ≥ 500 Sub-Skills |
| T03/T04 | `scenarios/t03-t04-parallel-builds.js` | Placeholder: refuses to run | none yet |
| T05 | `scenarios/t05-ingestion.js` | 20 VUs each announce and tus-upload one unique 15 MB synthetic `.txt`, then wait for PARSED and knowledge READY | every upload accepted; every document PARSED; < 1% failed requests; 0 × 5xx |
| T06 | `selftest/run_t06.sh` | T01 against a local stub with 800 ms latency | expects exit 99; a 0 ms control run must exit 0 |

The endpoints and the files they come from are listed at the top of each script.

## Why these numbers

- **API p95 ≤ 500 ms**: PLAN §28 "API p95: ≤ 500 ms for interactive endpoints"; #138 T01.
- **Error rate < 1%** and **no 5xx**: #138 T01 and the safety checks "Error rate < 1% during load"
  and "No 5xx or tenant leakage under concurrency".
- **Grid ≤ 2 s for 500 Sub-Skills**: PLAN §28 "ISATVON grid load ≤ 2 s for 500 Sub-Skills"; #138 T02.
- **10 users**: PLAN §28 "≥ 10 concurrent users (MVP)".
- **20 × 15 MB, all PARSED**: PLAN §28 "(20 files × 15 MB maximum)"; #138 T05.

## Configuration (env only)

| Var | Used by | Meaning |
|---|---|---|
| `BASE_URL` | all | API origin, e.g. `https://bam-perf.internal`. Routes are under `API_PREFIX` (default `/api/v1`). Call the API, not the portal's `/api/proxy`, which needs a browser session cookie. |
| `PROJECT_ID` | all | A synthetic perf project the login user is assigned to. T05 needs it **empty** (20 active files per project). |
| `WORKFLOW_ID` | T01, T02 | A workflow of that project. For T02 it must have ≥ 500 Sub-Skills. |
| `TOKEN` | all | A Keycloak access token (aud `sbam-api`). The realm issues 300 s tokens, so use it only for runs under ~4 min (T02, T06). |
| `LOGIN_USER`, `LOGIN_PASSWORD`, `KEYCLOAK_URL`, `WEB_URL` | all | Instead of `TOKEN`: each VU logs in the way the portal does (authorization code + PKCE on client `sbam-fde`; `KEYCLOAK_URL` is the realm URL, `WEB_URL` the portal origin used for the registered redirect URI) and refreshes its own token. Password grants are disabled on `sbam-fde`. Use this for T01 (15 min). |
| `TUS_URL` | T05 | Overrides the `tus_endpoint` the API hands out. |
| `VUS`, `DURATION`, `ITERATIONS`, `THINK_S`, `MIN_SUB_SKILLS`, `FILES`, `FILE_BYTES` | various | Overrides for short runs and the self-test. Do not change them for a release run. |

**Production guard.** Every script stops before any request if `BASE_URL`, `KEYCLOAK_URL`, `WEB_URL`
or `TUS_URL` contains `prod` (any case). Use a synthetic tenant, project and user only.

## Running against a deployed environment

```bash
export BASE_URL=https://bam-perf.internal PROJECT_ID=... WORKFLOW_ID=...
export LOGIN_USER=perf-fde LOGIN_PASSWORD=... KEYCLOAK_URL=https://kc.perf.internal/realms/bullm WEB_URL=https://bam-perf.internal

k6 run --summary-export=perf-t01.json tests/perf/scenarios/t01-interactive.js
k6 run --summary-export=perf-t02.json tests/perf/scenarios/t02-grid.js
k6 run --summary-export=perf-t05.json -e PROJECT_ID=<empty project> tests/perf/scenarios/t05-ingestion.js
```

Keep each summary JSON plus a Grafana snapshot of the run window as the release evidence (#138 outputs).

## Self-test (T06) and checks without k6

```bash
bash tests/perf/selftest/run_t06.sh        # needs k6 (or K6=/path/to/k6); exits 0 when all 3 runs behave
node tests/perf/selftest/init_check.mjs    # no k6: stubs k6/*, loads every scenario, asserts thresholds + guard
for f in tests/perf/lib/*.js tests/perf/scenarios/*.js; do node --check "$f"; done
```

`selftest/slow_server.py` (Python stdlib, 127.0.0.1 only) answers every `GET /api/v1/...` after
`--latency-ms` and returns a 500-Sub-Skill tree for `.../macro-skills`. `init_check.mjs` proves the
scripts load and carry the thresholds; only `run_t06.sh` with a real k6 proves the exit code.

## Not covered yet

- **T03/T04 (4 parallel builds, 5th queued)** need Temporal workers and the certifying CubeSandbox
  host; the placeholder lists the routes and the open questions.
- **Timings asked for in the 2026-11-18 update** (Temporal runner loop limits, workbench screens) have
  no agreed numbers. They are `null` in `targets.js` and nothing encodes them.
- **"SOW ≤ 5 min after knowledge READY"**: the update says the SOW timing predates the workflow
  pivot. T05 records `knowledge_ready_s` but has no threshold on it.
- **Grid render time**: T02 measures the data half. "+ render" needs a browser run (k6 browser or
  Playwright) on the workbench screen.
- **Tenant leakage under concurrency** is only covered indirectly (project reads must answer 200 for
  the assigned user). A second-tenant VU that expects 404 is not written yet.
- **Baselines**: the thresholds are release targets, not measured numbers. Measure them on the
  prod-like deploy VM (same image digests as the release, obs profile on) before trusting a breach.
- **Fixtures**: there is no seed for a 500-Sub-Skill workflow or a perf tenant/user; T02 fails its
  check on a smaller workflow rather than pass on it.
- **CI job**: none (`.github/**` was not touched).
