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
    older than timeout + 5 minutes that carries our metadata tag.

## Consequences

- The only host-specific inputs are `SANDBOX_MODE=CUBESANDBOX`, `CUBE_API_URL`, `CUBE_PROXY_NODE_IP` and the
  egress-log source. A worker in certifying mode refuses to start without them.
- `compose/netbird/policies.json` opens only :3000 (#142). It must also open CubeProxy, or a worker can create
  VMs but never run anything in them. Fixed with this change.
- `test_datasets`/`test_cases` (E13.4) and `test_runs` (E13.6) do not exist yet; the executor reads datasets
  through a `DatasetSource` protocol.
- CubeSandbox templates are memory snapshots, so every sandbox starts from the same RNG and clock state. Test
  code must not rely on process-start entropy.
