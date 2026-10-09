# Spike E0.12 (#30): Promptfoo against a FastMCP tool

## Question

Can SBAM's test stage (TD §15.1, T24) grade a FastMCP tool's outputs with a **deterministic** Promptfoo
suite (no LLM), from a pinned `sbam-promptfoo` container with no network, and turn the result into a
`test_runs` row?

## Setup

| Item | Value |
|---|---|
| Promptfoo CLI | `0.124.1` (needs Node ≥ 22.22), pinned in `spikes/promptfoo/promptfoo/package.json` |
| `sbam-promptfoo` image | `node:22.23-slim` + `npm install` of the pinned CLI; telemetry, update check and sharing disabled |
| Tool | FastMCP `4.1.0` on Python 3.12: MS01.1-shaped action tool `auth_verify_otp` + fixed check tool `rbac_check_policy` |
| Harness | `tool/harness.py`: cut-down TD §15.2 loop (no_op → passes ≤ `max_iterations` → success_gate / escalation) |
| Cases | `PASS_FIRST`, `ESCALATION`, `NO_OP` (the TD §15.1 minimum of 3) |
| Network | both containers `network_mode: none` |

How the output reaches Promptfoo: the `echo` provider returns its prompt unchanged, and the prompt is
`{{output}}` loaded from `file://out/outputs/<CASE>.json`. Promptfoo therefore never calls a model. It only
grades data the harness already wrote.

## Findings

| ID | Scenario | Result |
|---|---|---|
| E0.12-T01 | 3 valid cases | `status=PASSED total=3 passed=3`, exit 0 |
| E0.12-T02 | output without `state_handoff_id` | exit 1, `FAIL PASS_FIRST … output_schema: … must have required property 'state_handoff_id'` |
| E0.12-T03 | tool prints plain text | exit 1, first failure `output_schema: Expected output to be valid JSON`, no crash |
| E0.12-T04 | `tests: []` | exit 2, `INVALID_SUITE: suite has 0 test case(s); at least 3 required`. Promptfoo is not started. |
| E0.12-T05 | duration of a 3-case suite | about 2.3 s (warm), far below the 10 min per Sub-Skill budget. `duration_ms` is stored in the row. |
| E0.12-T06 | `--network none` | the suite needs no network by design (echo provider). `make t06` checks that the container cannot reach the internet. |

T01–T05 were measured with `make local` (Windows 11, Node 24, uv). The Docker targets (`run`, `t02`–`t04`,
`t06`) run the same files inside the two images. Record their output in the PR.

What we learned:

- **`transform` must never throw.** If `JSON.parse` throws inside an assertion `transform`, Promptfoo marks the
  whole test as an *error* and the `is-json` failure is hidden. A throwing `javascript` assertion only fails that
  assertion. So transforms use `try { … } catch { return "<not json>" }`. Returning `null` is also rejected
  ("Transform function did not return a value").
- **Name every assertion with `metric`.** The reason then says which check broke
  (`output_schema`, `iterations_within_bound`, …). This is what a correction prompt and the FDE need.
- **Promptfoo's exit code is not enough.** It is non-zero for any failure. The wrapper reads `results.json`
  (`results.results[].success` and `gradingResult.componentResults[]`) and uses its own exit codes
  (0 / 1 / 2 / 3).
- `promptfoo/package.json` is not exported (`ERR_PACKAGE_PATH_NOT_EXPORTED`), so the wrapper reads the version
  and bin path from `node_modules/promptfoo/package.json` directly.
- The suite hash is taken from the exact bytes Promptfoo reads (`suite_sha256`). The `test_runs` row shape:
  `suite_sha256, promptfoo_version, status, total, passed, failed, errors, duration_ms, results[{case, pass,
  error, failed_assertions[{metric, type, reason}]}], report_key`.

Safety checks:

- No network at run time: `network_mode: none`, echo provider, no API keys anywhere.
- The suite is hashed before the run.
- Outputs are data: tool output only ever reaches assertions as the `output` string. All JavaScript is in the
  platform-authored `suite.yaml`, and nothing is loaded from `out/`.

## Recommendation

Adopt this design for T24 `build_promptfoo_suite` / test evaluation:

1. Build `sbam-promptfoo` from `node:22.23-slim` with Promptfoo pinned **and a committed lockfile**. The spike
   pins only the top-level packages.
2. Generate the suite from the manifest (`o_outcome.json_schema`, `required_fields`, V bounds, N `ui_updates`).
   Use the echo provider and `file://` vars. Give every assertion a `metric`. Write safe transforms only.
3. Port the wrapper's logic into the test worker:
   - hash the suite (`suite_sha256`);
   - enforce ≥ 3 cases with the required scenario kinds;
   - map the results to `test_runs`;
   - store `results.json` via `StorageBackend` (`report_key`).
4. `llm-rubric` stays out of the default path. It needs the Bifrost alias, so it would need a network policy of its own.

No ADR needed: this confirms TD §15.1/§20 as written.
