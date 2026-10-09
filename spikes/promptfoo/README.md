# Spike E0.12 (#30): Promptfoo against a FastMCP tool

Throwaway prototype backing `docs/spikes/promptfoo.md`. Not product code and not part of
`compose/compose.yaml`. Synthetic data only, no LLM, no API keys.

## Layout

| Path | Purpose |
|---|---|
| `tool/ms01_tools.py` | FastMCP server with the MS01.1-style action tool (`auth_verify_otp`) and fixed check tool (`rbac_check_policy`) |
| `tool/harness.py` | Mini loop harness (TD §15.2): runs PASS_FIRST, ESCALATION, NO_OP and writes `out/outputs/<CASE>.json` (output + trace + handoff rows) |
| `suite.yaml` | Deterministic Promptfoo suite: is-json + JSON schema, equals, javascript (`iterations <= max_iterations`, …) |
| `empty_suite.yaml` | Suite with `tests: []`, for E0.12-T04 |
| `promptfoo/run_suite.mjs` | Wrapper: `suite_sha256`, ≥ 3 cases gate, runs Promptfoo, writes `out/test_run.json` (`test_runs` shape) |
| `promptfoo/package.json` | Pinned `promptfoo` 0.124.1 (+ `yaml` for the wrapper) |
| `tool/Dockerfile`, `promptfoo/Dockerfile` | Worker stand-in (Python 3.12 + FastMCP 4.1.0) and `sbam-promptfoo` (`node:22.23-slim`) |
| `docker-compose.yml` | Both services with `network_mode: none`, sharing `./out` |

## Run

```bash
make -C spikes/promptfoo run     # T01: 3 cases in Docker, no network -> status=PASSED total=3 passed=3, exit 0
make -C spikes/promptfoo t02     # missing state_handoff_id -> FAILED, names output_schema
make -C spikes/promptfoo t03     # plain-text output -> is-json fails, no crash
make -C spikes/promptfoo t04     # empty suite -> INVALID_SUITE, exit 2
make -C spikes/promptfoo t06     # promptfoo container cannot reach the internet
make -C spikes/promptfoo local   # same as run, without Docker (uv + Node >= 22.22)
make -C spikes/promptfoo clean
```

Wrapper exit codes: `0` passed · `1` an assertion failed · `2` suite invalid · `3` Promptfoo itself failed.
Read `out/test_run.json` for the stored row and `out/results.json` for Promptfoo's full report.
