# 0023 — Sandbox adapter: one backend protocol, the CubeSandbox SDK, a local runner, a vendored in-VM runner, evidence kept apart from enforcement

- Status: Accepted
- Date: 2026-10-29
- Related: issues #123 (E13.2), #122 (E13.1), refines [0012](0012-testing-sandbox-and-certification.md), [TD §15.2](../design/15-test-data-promptfoo-and-sandbox-design.md), [TD §20.5](../design/20-docker-and-environments.md)

## Context

`run_sandbox_tests` is a stub that always succeeds. The KVM host does not exist yet, but everything else
about the sandbox can be built and attacked now, so that giving a worker the host's address is the only
step left. CubeSandbox (`TencentCloud/CubeSandbox`, Apache-2.0, E2B-compatible) was read at commit
`0562294`; the facts below come from its `openapi.yml`, docs and Python SDK.

## Decision

1. **One protocol, two backends.** The executor talks only to `SandboxBackend` (`create`, `put_files`,
   `exec`, `get_file`, `destroy`, `list`). `CubeSandboxBackend` and `LocalDockerBackend` implement it. The
   mode comes from the worker's environment (`SANDBOX_MODE`) and from nowhere else; `SandboxMode.certifying`
   is the only place that says which mode may certify.
2. **The official `cubesandbox` SDK, pinned and hashed**, for both planes. Control (`create`, `kill`, `list`,
   `timeout`) is REST on CubeAPI :3000. Data (`exec`, files) is Connect RPC to the in-guest `envd`, through
   CubeProxy (:443/:80, gRPC :9090). A hand-written Connect client would drift from upstream in silence, so
   this is a deliberate exception to the minimal-dependency stance of [0018](0018-llm-wrapper.md). The SDK is
   synchronous: every call runs in `asyncio.to_thread`, behind our protocol, and a drift test pins the SDK
   signatures we use.
3. **Tests never run as root and never through string building.** The SDK defaults to `root` and runs
   `/bin/bash -l -c <cmd>`. We always pass `user="sbam"` (uid 10001) and a fixed command; every dynamic value
   travels as a file or an environment variable, never inside the command text.
4. **Template binding is content-derived and checked twice.** The pipeline gives each template the alias
   `sbam-rt-<first 56 hex of the skill-runtime digest>` (the alias limit is 64, so the key is a 224-bit prefix; the in-VM check compares the whole digest). The adapter resolves the alias of the
   manifest's digest; no alias means `TEMPLATE_DIGEST_MISMATCH`, never a stale template. Because an alias can be
   re-pointed by anyone with API access, the VM must also report the same digest in `/etc/sbam-runtime`, which
   the derived image writes at build time from its *base* digest.
5. **The template is a derived image.** `FROM skill-runtime@sha256:X` plus `envd` and its entrypoint copied from
   `cubesandbox-base` pinned by digest. Its own digest differs from X, so the registry digest X is the contract
   and the alias carries it.
6. **Local mode is a service, not a docker socket.** `compose.sandbox-local.yaml`'s `sandbox-runner` (internal
   network, read-only root, no capabilities, non-root, pids/memory/cpu limits) executes the runner. The worker
   never mounts `/var/run/docker.sock` (that is host root). Local isolation is weaker than a microVM (runs share a
   container), which is why its results are stamped `NON_CERTIFYING` in the run row, in the outputs and in the API,
   and why the certification checklist rejects any run whose mode is not `CUBESANDBOX`.
   The agent (`compose/sandbox-runner/agent.py`, stdlib only) gives the worker the same five verbs as a VM. Because
   the agent and the code it runs share one uid, it is non-dumpable (no `/proc` peeking at its token), a child
   subreaper that sweeps every descendant on `destroy`, resolves paths by their real location, and holds **one run
   at a time** so two runs never share a filesystem view. Throughput on a laptop is therefore one run at a time.
7. **The in-VM runner is vendored, not installed.** The E13.3 harness and what it imports are copied into
   `/opt/sbam/harness` (the slot the image already reserves) and put on `PYTHONPATH`, not installed into the
   virtualenv, because the runtime registry is generated from what is installed and must not list platform code.
   A test fails when the vendored file list stops matching the import closure.
8. **Enforcement and evidence are different things.** Egress is enforced in the data plane (CubeVS eBPF,
   CubeEgress). CubeSandbox's audit log is a host-local JSONL file and covers HTTP and HTTPS only; L3/L4 drops
   are not logged. `EgressEvent` therefore carries its `source` (`L7_PROXY`, `GUEST_PROBE`, `HOST_TAP`) and a
   verdict that may be `UNKNOWN`. A certifying run requires a configured egress-log source; whether L3/L4
   evidence is complete is decided by the host acceptance suite (E13.1-T03, T04), not assumed.
9. **Failures are classified by evidence, in one place.** Test failures (`IMPORT_ERROR` … `EGRESS_BLOCKED`) end in
   `TestsFailed` and spend a correction; infrastructure failures (`INFRA_ERROR`) end in `SandboxUnavailable`,
   retry three times, then `TESTING_BLOCKED`, and never spend a correction. The rules live in `classify.py` and
   are tested both ways, because a wrong answer either burns corrections on a dead host or retries a real bug
   forever.
10. **Idempotent, and nothing outlives its run.** `sandbox_runs` is unique on `(code_attempt_id, dataset_id,
    run_no)`; a retried activity finds its row. The VM is destroyed in `finally`; an orphan reaper destroys any VM
    older than its own TTL + 5 minutes that carries our run tag and our environment.

11. **What the harness needs is derived from the manifest, not from the bundle.** Tool names, the fresh-state
    names, the key of the fresh-state queries, the handoff fields and the Sub-Skill's UI messages come from the
    manifest's render params (`inputs.harness_config`), the same source the bundle was generated from. A bundle
    that drifted from what it was asked to be fails its tests; it cannot redefine them.
12. **The in-VM runner** (`compose/sandbox-runner/harness/run_cases.py`) starts an ephemeral PostgreSQL, applies
    `schema.sql`, gives each case its own database made from it, seeds the fresh state, imports its own copy of
    the bundle and drives the E13.3 harness against the bundle's FastMCP server in memory. Its rules, each with
    a test in the real container: exit 0 outputs written (cases may have failed), 71 database setup failed, 72
    the dataset cannot run, 70 the runner crashed; the bundle appends its own handoff rows and the runner only
    checks them (exactly one new row per pass, every required field); every call is cut down to the parameters
    the tool declares; a call that overruns is abandoned, not awaited, because FastMCP will not cancel a
    synchronous tool; `SystemExit` and `KeyboardInterrupt` from a tool fail the case; a tool that ran out of
    memory is marked `OOM`; the outputs file is written atomically and fitted to the size cap.
13. **Time.** The runner gets `min(case limit x cases + 30 s, 18 min)`, below the stage's 20 minute activity
    timeout, so an overrun ends as a TIMEOUT of the code with the VM destroyed and the evidence stored. The case
    limit follows the Sub-Skill's own bound (`max_iterations x 2 x budget_ms`), never below 60 s; a bound that
    needs more than the policy's 300 s is a final `INPUT_MISMATCH`, never clamped. The activity beats three times
    inside the heartbeat timeout.
14. **Evidence.** A run that reached a VM stores its outputs, logs and egress events content-addressed and
    indexes them in `artifacts` (`SANDBOX_OUTPUTS`, `SANDBOX_LOGS`, `SANDBOX_EGRESS`). A test outcome writes the
    LOOP_TESTS gate row the corrector reads (`failed_for_pass`); an infrastructure error writes none, because it
    says nothing about the code. The run row stores the mode; `SandboxRun.certifying` (mode is CUBESANDBOX) is
    the one test for whether a run may count towards certification, and a finished run is only replayed in the
    mode it ran in.
15. **Checked before anything runs.** Every code gate passed *for this bundle*, the schema is the one the manifest
    names, the dataset is synthetic with at least 3 cases including PASS_FIRST and ESCALATION, every stored
    object still hashes to its key. A worker with a half-set sandbox refuses to start (`SANDBOX_MISCONFIGURED`);
    a deployed environment refuses LOCAL_DOCKER_NONCERT; a CubeSandbox template bigger than the run's limits is
    refused and its VM destroyed.

## Consequences

- The only host-specific inputs are `SANDBOX_MODE=CUBESANDBOX`, `CUBE_API_URL`, `CUBE_PROXY_NODE_IP` and the
  egress-log source. A worker in certifying mode refuses to start without them.
- `compose/netbird/policies.json` opened only :3000 (#142), so a worker could create VMs and never run anything
  in them. CubeProxy :80 is open now (`sandbox-proxy`), with the matrix test.
- The executor reads datasets through the E13.4 types (`CaseBody`, `dataset_sha256`) and stores its evidence
  next to `test_runs` (E13.6): `test_runs.sandbox_run_id` points at the run, and the stage that writes a
  `test_runs` row must set `non_certifying` from `SandboxRun.certifying`.
- CubeSandbox templates are memory snapshots, so every sandbox starts from the same RNG and clock state. Test
  code must not rely on process-start entropy.

### Known limits (not fixed here, each with its issue or its owner)

- The code under test shares a process with the harness, so it can forge its own verdict, and `os._exit` reads
  as a dead runner: [#625](https://github.com/solutionsDigibull/BAM/issues/625).
- The key of fresh state follows JSONB property order: [#624](https://github.com/solutionsDigibull/BAM/issues/624).
- `HarnessTrace.to_json_object()` drops `terminal: null`: [#626](https://github.com/solutionsDigibull/BAM/issues/626).
- Never run against a real CubeSandbox: microVM boot, CubeVS egress enforcement, the egress collector, DNS
  rebinding of an allowlisted name and the host's OOM killer. The host acceptance suite (#122) proves them;
  until it has run they are untested, not passing. `docs/runbooks/sandbox-host.md` lists what the host must
  provide.

## Evidence: what attacking each layer found

Every row has the test that failed before the fix and passes now.

| # | Layer | Flaw | Fix |
|---|---|---|---|
| 1 | contracts | `$` matches before a trailing newline: two spellings of one digest, label or TLD | `fullmatch` |
| 2 | contracts | `sbam-rt-` + 64 hex is 72 characters; CubeSandbox's alias limit is 64 | 56-hex prefix; the in-VM check compares all 64 |
| 3 | Netbird (#142) | only CubeAPI :3000 was open: a worker could create VMs and never run a test | `sandbox-proxy` :80 |
| 4 | database | a CHECK passes on NULL: an `infra_reason` with no failure category was accepted | `COALESCE(..., false)` |
| 5 | database | `claim` was not concurrency-safe: 14 of 16 racing claimers failed | advisory lock per (tenant, attempt, dataset) |
| 6 | worker policy | the planner capped the resolver's raw answer, so a duplicate took a slot | `sorted(set(...))[:cap]` |
| 7 | local agent | test code read the agent's token from `/proc/1/environ` | non-dumpable, token popped from the environment |
| 8 | local agent | the agent set its own `PATH`: every real run would fail to import `fastmcp` | the image's `PATH` |
| 9 | local agent | runs shared a uid and a filesystem | one run at a time |
| 10 | local agent | a symlink planted by test code read and wrote outside the VM directory | `realpath`, `O_NOFOLLOW` |
| 11 | local agent | a background process outlived its command, and the next run | process group swept, subreaper |
| 12 | runner | the vendored `_jsonschema` and `canonical` need packages the image deliberately lacks | stand-ins that refuse to run |
| 13 | runner | the harness calls tools with `iteration_pass` and the action's outputs; generated tools reject both | calls cut to the declared parameters |
| 14 | runner | the harness composes the handoff row from tool outputs; real tools write their own row | the runner checks the table instead |
| 15 | runner | a synchronous tool that overran blocked cancellation for its whole sleep (70 s) | abandon the call, never close the loop |
| 16 | runner | `SystemExit` in a tool hung the runner at interpreter shutdown for the full deadline | caught per case; `os._exit` at the end |
| 17 | runner | a `MemoryError` reaches the harness as an empty message | a FastMCP middleware marks it `OOM` |
| 18 | runner | a late thread of a timed-out tool could touch the next case's module state | each case imports its own copy |
| 19 | activity | worst case 20 cases x 60 s = 1230 s, past the 20 minute activity timeout | run capped at 18 minutes |
| 20 | activity | heartbeat every 30 s with a 30 s heartbeat timeout | three beats inside the timeout |
| 21 | activity | a finished local run could answer a certifying worker's retry | replay only in the same mode |
| 22 | activity | a tampered stored dataset file read as a flaky read (retried) | `HashMismatch`, non-retryable, security log |
| 23 | backend | a VM started with an epoch timestamp could never be reaped | the SDK's own parser |
| 24 | backend | the template, not the call, sets a VM's size: a big template voids the limits | refused, VM destroyed |
| 25 | activity | a loop bound over the policy ceiling was a retryable infrastructure error | final `INPUT_MISMATCH` |
