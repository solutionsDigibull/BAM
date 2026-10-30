# Sandbox: the CubeSandbox host and the local runner

Where Sub-Skill tests run (E13.2, [ADR 0023](../adr/0023-sandbox-adapter.md)). The worker on the `sbam-test` queue runs one
code attempt against one dataset in one sandbox and records a `sandbox_runs` row. This page is what an operator needs to
give a worker its sandbox, and what to do when tests stop running.

## Symptoms

- A Sub-Skill sits in **TESTING_BLOCKED** and the FDE sees the notification "Testing blocked". The build retried the sandbox
  stage three times; no correction was used. `retry` re-runs only the sandbox tests.
- A run row says `INFRA_ERROR` with an `infra_reason` (below). A row that says anything else (`IMPORT_ERROR`,
  `RUNTIME_ERROR`, `TIMEOUT`, `OOM`, `OUTPUT_SCHEMA_MISMATCH`, `ASSERTION_FAILED`, `EGRESS_BLOCKED`) is a verdict on the code
  and goes to the corrector: it is not this runbook.
- The test worker does not start and logs `SANDBOX_MISCONFIGURED` (exit 3).

## Impact

No Sub-Skill reaches certification while testing is blocked. Builds of other Sub-Skills continue up to the test stage and
wait there. Nothing is lost: the run, its evidence and the correction counter are intact.

## Configuration

The mode comes from the worker's environment and from nowhere else: no API or UI field can pick it. Empty means no sandbox.

| Variable | Used by | Meaning |
|---|---|---|
| `SANDBOX_MODE` | both | `CUBESANDBOX` (certifying) or `LOCAL_DOCKER_NONCERT` (laptops; refused when `SBAM_ENV` is not development, test or ci) |
| `CUBE_API_URL` | CubeSandbox | CubeAPI, e.g. `http://<netbird address>:3000` (control plane) |
| `CUBE_PROXY_NODE_IP`, `CUBE_PROXY_PORT_HTTP` | CubeSandbox | CubeProxy, port 80 (data plane: exec and file upload; the SDK uses plain HTTP) |
| `CUBE_SANDBOX_DOMAIN` | CubeSandbox | `cube.app` unless the host was installed with another domain |
| `CUBE_API_KEY` | CubeSandbox | only when the host enables authentication (it does not by default) |
| `SANDBOX_EGRESS_LOG_PATH` | CubeSandbox | the file of `EgressEvent` lines the host collector writes (required: no evidence, no certification) |
| `SANDBOX_AGENT_URL`, `SANDBOX_AGENT_TOKEN` | local | the `sandbox-runner` agent (`make up PROFILES=sandbox-local`) |
| `SANDBOX_RUNTIME_DIGEST` | local agent | the digest the local runtime reports; `make runtime-registry` prints it |
| `MOCK_API_URL`, `MOCK_API_IP`, `MOCK_CONTROL_TOKEN` | both | the mock API for networked sources; the IP must be an address that carries nothing else (CubeSandbox opens every port of an allowed address) |

A worker with `SANDBOX_MODE` set and something missing refuses to start and names the variable (never its value).

## What the CubeSandbox host must provide

Written from the CubeSandbox source and the SDK. **None of it has been run against a real host**: the host acceptance suite
(#122, E13.1-T01..T09) proves it, and until that has run these are requirements, not facts.

1. **Reachability.** CubeAPI :3000 and CubeProxy :80 from the VMs' workers over Netbird (`vms-to-sandbox-api` in
   `compose/netbird/policies.json`; `python3 scripts/netbird_matrix.py` prints the matrix). The host initiates nothing.
2. **A template per runtime digest**, with the alias `sbam-rt-<first 56 hex of the skill-runtime digest>`, built
   `FROM skill-runtime@sha256:<digest>` plus `envd`. The adapter resolves the alias for the manifest's digest; no alias is
   `TEMPLATE_DIGEST_MISMATCH`.
3. **`/etc/sbam-runtime`** in the template holds the full base digest. The adapter reads it as root and compares it with the
   manifest's; a re-pointed alias is caught here.
4. **The unprivileged user `sbam` (uid 10001)**. Tests run as it, never as root. Files the platform writes (`/etc/hosts`) are
   written as root.
5. **`/opt/sbam/harness`** holds the runner and its vendored packages, root-owned and read-only. `compose/docker/sandbox-runner.Dockerfile`
   shows the build stage (`compose/sandbox-runner/harness/stage.py`); the template needs the same copy. `files.txt` there is the exact import
   closure; a unit test fails when it drifts.
6. **PostgreSQL 16 and `sbam-pg-start`** (already in `skill-runtime`).
7. **Size.** The SDK cannot set CPU or memory; the template does. The adapter reads the VM's size after creating it and refuses
   (and destroys) a VM bigger than the run's limits (1 vCPU, 1 GiB by default).
8. **Egress evidence.** CubeSandbox's audit log (`/data/log/cube-egress/access.jsonl`) covers HTTP and HTTPS only and carries
   the VM's IP, not its id; connections dropped at L3/L4 are not in it. The host collector joins that log with the VM's address and
   with what it captures on the VM's tap device, and writes one `sandbox/egress_event` line per attempt to `SANDBOX_EGRESS_LOG_PATH`.
   The worker only reads that file. Without it a certifying run ends `EGRESS_LOG_UNAVAILABLE`.
9. **Allowlisted names.** An egress entry is `host:port`; the adapter resolves each name and refuses a non-public answer
   (`EGRESS_BLOCKED`). A rebind after that check can only be ruled out on the host (E13.1-T06).

## Diagnosis

```sql
-- the newest runs of a Sub-Skill's attempt, with why
SELECT run_no, mode, status, failure_category, infra_reason, vm_id, started_at, finished_at
FROM sbam.sandbox_runs WHERE code_attempt_id = '<attempt>' ORDER BY run_no;
```

The logs of a run: `GET /api/v1/projects/{p}/workflows/{w}/sandbox-runs/{run}/logs` (302 to a signed attachment). `summary`
on the row lists each case's verdict, never what the code printed.

| `infra_reason` | Means | Look at |
|---|---|---|
| `NOT_CONFIGURED` | the worker has no sandbox mode or host settings | the worker's environment (above) |
| `HOST_UNREACHABLE` | CubeAPI, CubeProxy or the agent did not answer | Netbird policy and peers, the host's services, `make logs s=worker-test` |
| `TEMPLATE_DIGEST_MISMATCH` | no template for this runtime digest, or the VM runs another | the host's template list; `/etc/sbam-runtime` in the template |
| `VM_CREATE_FAILED` | the host refused or the template is too big for the limits | CubeAPI logs; the template's CPU and memory |
| `RUNNER_FAILED` | the runner died or wrote nothing usable (also code calling `os._exit`, #625) | the run's logs; `exit_code` on the row |
| `DATASET_INVALID` | a seed statement failed, or a data file name is not a file name | fix or regenerate the dataset (E13.4); not a host problem |
| `INPUT_MISMATCH` | a gate did not pass for this bundle, or the schema is not the manifest's | the gate rows; rebuild |
| `HASH_MISMATCH` | an input changed after the gates: **a security event**, `alert=security` in the worker log | who touched storage; do not retry blindly |
| `EGRESS_LOG_UNAVAILABLE` | the evidence file is missing or unreadable | the collector, `SANDBOX_EGRESS_LOG_PATH` |
| `SUPERSEDED` | a retry took over a stalled run | normal after a worker restart |
| `CANCELLED` | the activity was cancelled (deploy drain, `cancel` signal) | normal |

A VM that was not destroyed logs `a sandbox VM was not destroyed` (`alert=operator`) with its `vm_id`. The reaper (every five
minutes, `reap-sandbox-vms` Temporal Schedule on `sbam-test`) destroys VMs that carry our run tag and environment and are older
than their own TTL plus five minutes, and logs each one.

## Fix

- Configuration: set the variable, restart `worker-test`. The self-check names what is missing.
- Host down: bring it back; the FDE's `retry` (or the next build) resumes. Nothing needs to be re-run by hand.
- A leaked VM the reaper cannot destroy: destroy it through CubeAPI (`DELETE /sandboxes/<vm_id>`), then check `failed` in the reaper's log line.
- `HASH_MISMATCH`: treat as an incident. The bundle, `schema.sql` or the dataset was changed in storage after it was recorded.

## Verification

- `make up PROFILES=sandbox-local` and a build run end to end on a laptop (results stay `LOCAL_DOCKER_NONCERT`).
- `uv run pytest -m integration tests/integration/test_e13_2_runner_container.py tests/integration/test_e13_2_activity_stack.py`
  runs the hardened runner container, the real stage and Postgres (needs Docker and `skill-runtime:local`).
- On the host: the acceptance suite of #122. A certifying run is one whose row says `CUBESANDBOX`; the certification checklist
  refuses every other.

## Escalation

Platform owner (D3). A run that certifies on a host that was not accepted is a certification defect: stop certifying and open
an issue with the run id.
