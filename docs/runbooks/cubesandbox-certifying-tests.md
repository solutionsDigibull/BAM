# The certifying CubeSandbox run: E13.6-T02 and E14.1-T01

Two integration cases can only be shown on a CubeSandbox (KVM) host, because a local-docker run is
NON_CERTIFYING by design: **E13.6-T02** (MS01.1 happy 3/3, badge PASS 3/3, #127) and **E14.1-T01** (full
evidence certifies, #129). Both live in `server/tests/integration/test_e13_6_e14_1_cubesandbox.py`. Everywhere
else (laptops, CI's compose-smoke) they **skip**, naming the settings that are missing. When
`SANDBOX_MODE=CUBESANDBOX` is set they never skip: anything missing or wrong **fails**.

What runs: the pytest process is the test worker. It seeds one MS01.1 build (approved SOW, DRAFT manifest, the
bundle generated from it, every gate PASSED on that bundle, a PASS_FIRST / ESCALATION / NO_OP dataset) in a scratch
database on the stack's Postgres. It then runs the real `run_sandbox_tests` activity with the real sandbox factory
(the `CubeSandboxBackend` and your egress reader) and the real judge, reads the badge, runs the real
`CertifySubSkill` and applies `tests_passed` through the state machine. It reaches CubeAPI and CubeProxy itself, on
loopback. `worker-test` and `compose/compose.sandbox-cube.yaml` are not used: that overlay points a container at a
WSL2 host (`host.docker.internal`, proxy port 80, an empty egress file), and the Makefile has no `sandbox-cube`
profile (`make up PROFILES=sandbox-cube` is refused; the valid profiles are `obs analytics sandbox-local`).

## Symptoms

The two cases show `SKIPPED ... CubeSandbox is not configured` on CI and on laptops. That is expected. Issues #127
and #129 stay open until a host owner records a run here.

## Run it (on the Linux/KVM host of `docs/spikes/cubesandbox.md`)

1. Start the guest and check it (spike, "Bring-up"). Never run `relay.sh`.

   ```bash
   cd ~/cubesandbox-host/CubeSandbox/dev-env
   VM_BACKGROUND=1 VM_MEMORY_MB=4096 VM_CPUS=2 \
   EXTRA_FORWARDS="13000:3000 18080:18080 12088:12088 11443:443" ./run_vm.sh
   curl -s http://127.0.0.1:13000/health            # {"status":"ok",...}
   ~/cubesandbox-host/g.sh "/usr/local/bin/cubemastercli tpl ls"   # sbam-rt-<56 hex> READY
   ```

2. Bring up the core stack from a checkout of the branch under test (Postgres is what the cases need):

   ```bash
   make up
   ```

   About 6 GB of free RAM is needed for the guest and the stack together (spike, "Resources").

3. Run both cases with **one command** from `server/`:

   ```bash
   cd server && \
   SANDBOX_MODE=CUBESANDBOX \
   CUBE_API_URL=http://127.0.0.1:13000 CUBE_PROXY_NODE_IP=127.0.0.1 CUBE_PROXY_PORT_HTTP=18080 \
   CUBE_SANDBOX_DOMAIN=cube.app \
   SANDBOX_EGRESS_URL=<collector url> SANDBOX_EGRESS_TOKEN=<collector token> \
   SBAM_CUBE_RUNTIME_DIGEST=sha256:<64 hex> \
   uv run pytest -m integration -rA -v tests/integration/test_e13_6_e14_1_cubesandbox.py 2>&1 | tee cube-certifying-run.txt
   ```

   | Variable | Value on this host |
   |---|---|
   | `SANDBOX_MODE` | `CUBESANDBOX`: the opt-in. Without it the cases skip |
   | `CUBE_API_URL`, `CUBE_PROXY_NODE_IP`, `CUBE_PROXY_PORT_HTTP`, `CUBE_SANDBOX_DOMAIN` | the loopback forwards of the spike ("SBAM side") |
   | `SANDBOX_EGRESS_URL` + `SANDBOX_EGRESS_TOKEN`, or `SANDBOX_EGRESS_LOG_PATH` | the read-only service of `cubesandbox-host/egress` (`GET <url>/egress/<vm_id>`), or the JSONL file it writes. The spike does not record a loopback port for it; do not use the `0.0.0.0:29000` that `relay.sh` opens. A run with no evidence ends `EGRESS_LOG_UNAVAILABLE` and fails |
   | `SBAM_CUBE_RUNTIME_DIGEST` | the full digest the READY template was built from, as its `/etc/sbam-runtime` says. On 2026-11-19 that was `sha256:d9ec93ae65b08532063f23cd9b855aa559debbe881ff0f4fa85f19fd4fa59fed` (spike T01); check it again |
   | `CUBE_API_KEY` | only if your CubeAPI wants one |

4. Shut the guest down cleanly afterwards: `~/cubesandbox-host/g.sh poweroff`, and `make down` if you want the
   RAM back.

## Verification: what a pass looks like

```
tests/integration/test_e13_6_e14_1_cubesandbox.py::test_e13_6_t02_happy_3_of_3_badge_pass_cubesandbox PASSED
tests/integration/test_e13_6_e14_1_cubesandbox.py::test_e14_1_t01_full_evidence_certifies_cubesandbox PASSED
```

Each case also prints one evidence line (shown by `-rA` under "Captured stdout"): `E13.6-T02 {...}` with the
sandbox run id, `mode: CUBESANDBOX`, the VM id, the runtime digest, the test run id, `total 3 passed 3 state PASS`,
the grid's badge and how many seconds after the `test_runs` insert it was read; `E14.1-T01 {...}` with the
certification id, the content hash, the manifest status and `sub_skill_state: CERTIFIED`.

`SKIPPED` means `SANDBOX_MODE` did not reach the process: it is not a result. Anything else is a failure to report.

## Diagnosis

| Failure | Look at |
|---|---|
| `Compose stack is not running` | `make ps`; the stack's Postgres must be published on the host |
| `the CubeSandbox adapter is not fully configured: ... not set` | the variables above |
| `SBAM_CUBE_RUNTIME_DIGEST must be ...` | the full `sha256:` and 64 hex characters, not the alias |
| `SandboxUnavailable: ...` with a printed `sandbox_run ... INFRA_ERROR <reason>` | the Diagnosis table of [sandbox-host.md](sandbox-host.md#diagnosis): `HOST_UNREACHABLE` (guest down, wrong ports), `TEMPLATE_DIGEST_MISMATCH` (digest or template), `EGRESS_LOG_UNAVAILABLE` (collector) |
| `TestsFailed` | the bundle or the judge failed a case: the `test_runs` results in the assertion message name the case and the assertion |
| `GET /projects/{p}/workflows/{w}/builds does not show the Loop tests badge` | the grid row's `loop_tests`. On `dev` at the time of writing `builds.dashboard._row` does not fill it, so E13.6-T02 fails here until it does; the sandbox, `test_runs` and service-badge checks before it have then passed |
| `CertificationBlocked: ...` | the reasons it names (`MISSING_EVIDENCE:*`, `NON_CERTIFYING_RUN`, `SUITE_CHANGED`, ...), see `certify/checklist.py` |

## Fix, and recording the result in #127 and #129

Fix what the table names and run the command again: each run seeds a new tenant in a new scratch database, which
is dropped at the end.

The issues are owned by their assignee; a run is recorded as a comment, not by ticking boxes or closing anything.
Paste the same block into **#127** (for E13.6-T02) and **#129** (for E14.1-T01):

````markdown
Certifying run (CubeSandbox, mode CUBESANDBOX) of `test_e13_6_e14_1_cubesandbox.py`: E13.6-T02 <PASSED|FAILED>, E14.1-T01 <PASSED|FAILED>.
Host: Linux/KVM, CubeSandbox guest per docs/spikes/cubesandbox.md, loopback only, no relay.sh. Date: <UTC date>.
Commit: <git rev-parse HEAD>. Template: sbam-rt-<56 hex>, digest <SBAM_CUBE_RUNTIME_DIGEST>. Egress evidence: <URL service | file>.

<details><summary>pytest -rA output</summary>

```
<contents of cube-certifying-run.txt>
```
</details>
````

Never paste the egress token or any key. If a case failed, paste the output as it is and say which row of the
diagnosis table applies.

## Escalation

CubeSandbox host and adapter: the E13.2 owner (#123) and [sandbox-host.md](sandbox-host.md). The judge and the
badge: #127's assignee. Certification: #129's assignee.
