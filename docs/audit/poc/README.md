# Reproducing the evidence

Everything here ran against **throwaway local resources only**: a disposable Postgres 16 container, the API from this
tree on a loopback port, a local JWKS and RSA key to mint tokens, and the repo's own fake clamd. Do not point these
scripts at a shared or production environment. They create tenants, users, projects and audit rows.

| Script | Shows |
|---|---|
| `poc_ast_bypass.py` | F-02: codegen `precheck()` returns no findings for 6 Python escapes and 3 SQL queries (run with `uv run --frozen --all-packages python -I`) |
| `live/pentest1.py`, `live/pentest2.py` | ~90 checks: token attacks, authz matrix and IDOR, F-04, F-05, F-08/N-05, tusd hooks, signed URLs, limits, cursors, headers |
| `live/bugA.py` | N-01: 45 of 120 requests fail with 500 after simulated logins |
| `live/thread_exhaust.py`, `live/hung_clamd.py` | N-02: 45 parallel imports against a hung clamd stall the API |
| `live/xlsx_dos.py`, `live/impact.py` | N-03: 14 MB workbook cost and effect on other users' latency |
| `pdf/mk.py`, `pdf/run.py` | F-24: crafted PDFs against pypdf 6.20.1 vs 6.21.2 (`uv run --no-project --with pypdf==<v> python -I run.py x.pdf`) |
| `semgrep.json`, `bandit.json`, `pnpm-audit.json`, `pipaudit.json` | Raw scanner output |

## Harness setup (what the `live/` scripts expect)

1. `docker run -d --name audit-pg -e POSTGRES_PASSWORD=x -p 127.0.0.1:55999:5432 postgres:16.15-alpine`, then create
   role `sbam` (password `auditpw`) and database `sbam` owned by it, as `compose/postgres/init.sh` does.
2. `cd server && source ../docs/audit/poc/live/env.sh && uv run python -m sbam_api.db.migrate`
3. Start `live/jwks_server.py` (serves the public key on 127.0.0.1:55998; `lib.py` creates `key.pem`, never commit it).
4. Start the API: `uvicorn sbam_api.main:create_app --factory --host 127.0.0.1 --port 55997`, with `CLAMAV_HOST` and
   `CLAMAV_PORT` pointing at `live/clamd_server.py` (prints its port) for the import tests.
5. Run the scripts with `.venv/bin/python -I`. The scripts print `OK`/`VULN` per check. Two checks encode a
   *policy* (e.g. "OpenAPI requires auth") and are informational where the finding says so.

Track background processes by PID file; do not use `pkill -f` patterns (a broad pattern can match unrelated processes
and your own shell).
