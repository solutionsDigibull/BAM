# Spike E0.10 (#28): CubeSandbox on a Linux/KVM host

> **Status: executed LIVE on 2026-11-19** on this Linux/KVM host (Arch Linux, nested KVM), against **one guest,
> one warm template (`sbam-rt-d9ec93ae...`), over loopback only**. T01-T08 below carry measured results. Read the
> Caveats block before relying on the numbers.

## Question

Can SBAM run a generated tool in a CubeSandbox VM built from a pinned `skill-runtime` image: a template made
from the image, a FastMCP tool run inside it, an egress default-deny that is enforced and logged, a start
latency compatible with the 10 minute Sub-Skill target, and no state shared between runs?

## Setup

What this report is based on (read first, then run live on 2026-11-19):

| Item | Value |
|---|---|
| CubeSandbox source and one-click install | `CubeSandbox/` checkout on the developer host (`deploy/one-click/`, `dev-env/`) |
| Host files | `cubesandbox-host/`: `g.sh`, `relay.sh`, `add-runtime-template.sh`, `template-build/Dockerfile`, `egress/` (collector and read-only HTTP server), `smoke/` probes, `monitor/monitor.log` |
| Earlier evidence | `docs/runbooks/sandbox-host.md`, `compose/compose.sandbox-cube.yaml`, PR #774 (template and `worker-test` config), PR #709 (egress evidence over HTTP) |
| Runtime images present on the host | `skill-runtime` `sha256:d9ec93ae65b0...fed` and `sha256:bb83fcd1dd3c...a079`, each with a derived `localhost:5000/sbam/runtime:sbam-rt-<digest>` image |

## Architecture

Cube does **not** run on the host. It runs inside a disposable **QEMU/KVM guest** (OpenCloudOS 9, a prebuilt
qcow2 under `dev-env/.workdir/`, already installed with the one-click package). Inside the guest are CubeAPI,
CubeMaster, Cubelet, CubeProxy, CubeNet, CubeEgress, the web console and their MySQL, Redis and MinIO containers.
The sandboxes are MicroVMs inside that guest, so the guest needs nested KVM.

The host side is only:

- one `qemu-system-x86_64` process with **user-mode networking** (`-nic user`) and loopback port forwards;
- the local plain-HTTP registry container `cube-registry` (`registry:2`, `:5000`): template images are pushed
  there and the guest pulls them from `10.0.2.2:5000`;
- the helper scripts: `g.sh` (ssh into the guest on `127.0.0.1:10022` with the `id_cube` key, then `sudo` **inside
  the guest**), `add-runtime-template.sh`, and `relay.sh`.

There is no host root, no host nftables or iptables, no bridge or tap on the host. The firewall rules and the
sandbox tap devices exist only inside the guest.

## Bring-up (read from the source, then run live)

```bash
cd ~/cubesandbox-host/CubeSandbox/dev-env
VM_BACKGROUND=1 VM_MEMORY_MB=4096 VM_CPUS=2 \
EXTRA_FORWARDS="13000:3000 18080:18080 12088:12088 11443:443" ./run_vm.sh
```

`EXTRA_FORWARDS` is how `run_vm.sh` publishes guest ports, and its `HOSTFWD_LIST` binds them to `127.0.0.1`
unless `EXTRA_BIND` is set. The forwards match the ones in the earlier `vm-boot.log`. `VM_BACKGROUND=1` daemonizes
QEMU (default is a foreground serial console). The defaults are 8192 MB and 4 vCPU; this spike proposes 4096 MB and
2 vCPU (see Resources).

| Host (loopback only) | Guest | What |
|---|---|---|
| `127.0.0.1:10022` | 22 | ssh (`g.sh`, `login.sh`) |
| `127.0.0.1:13000` | 3000 | **CubeAPI** (not 23000) |
| `127.0.0.1:18080` | 18080 | CubeProxy (exec and file upload) |
| `127.0.0.1:12088` | 12088 | web console |
| `127.0.0.1:11443` | 443 | TLS |

- **Health:** `curl http://127.0.0.1:13000/health` should answer `{"status":"ok","sandboxes":0}`.
- **A shell in the guest:** `./login.sh` (or `~/cubesandbox-host/g.sh '<command>'` for one command as guest root).
- **Services:** expected to start with the guest (the one-click installer made them systemd units such as
  `cube-sandbox-cube-api` and `cube-sandbox-compute.target`, plus the guest's own containers). This is **not
  confirmed until the VM boots**: check `g.sh "systemctl list-units 'cube-sandbox*'"`.
- **SBAM side:** `CUBE_API_URL=http://127.0.0.1:13000`, `CUBE_PROXY_NODE_IP=127.0.0.1`, `CUBE_PROXY_PORT_HTTP=18080`,
  `CUBE_SANDBOX_DOMAIN=cube.app`. The spike drives the Cube SDK from a script (`smoke/`), so `compose/.env` is not
  touched and no worker is given an outbound route.
- **Run live on 2026-11-19** exactly as written (with `relay.sh` left off). The guest was powered off cleanly afterwards (`g.sh poweroff`).

Differences from the runbook: `docs/runbooks/sandbox-host.md` describes a WSL2 host (CubeAPI on 13000 through
`host.docker.internal`). This host is Linux with the QEMU guest above; the ports and the loopback forwards differ,
the template and the egress contract are the same.

## What it changes, and reversibility

| Change | Where | Reversal |
|---|---|---|
| A QEMU process, 4 vCPU and 8 GB RAM by default | host | `./login.sh`, then `poweroff` in the guest. Do **not** stop QEMU abruptly (`Ctrl+a x`, `kill -9`): the dev-env README warns that it can corrupt the qcow2 |
| Loopback port forwards (table above) | host | gone with the QEMU process |
| Template images pushed to `localhost:5000` | host registry container | `docker image rm`, or stop `cube-registry` |
| Firewall, taps, CubeNet rules, containers | **guest only** | gone with the guest |
| No host root, no host nftables, no kernel change | | `/dev/kvm` is already world-accessible and nested virtualization is already on |

**`relay.sh` is deliberately not used.** It starts `socat` listeners on `0.0.0.0:23000`, `28080` and `22088` and the
egress server on `0.0.0.0:29000`, which puts the control plane and the egress evidence on the LAN. It exists to serve
another machine on the same Wi-Fi and is the only hard-to-undo exposure in the setup. `relay.sh stop` removes it.

## Resources

- A guest of 2 vCPU and **4 GB** is the minimum for sandboxes of 1 GiB and 1 vCPU (the limits in the issue). The
  one-click default is 8 GB.
- This host has 14 GB RAM and, with the SBAM stack and other containers up, about **3 GB free** (checked 2026-11-19).
  The live run was done after the SBAM and other stacks were stopped to free RAM (about 6 GB available); booting
  the guest alongside them risks an out-of-memory kill of the SBAM stack.
- Disk: the qcow2 is already allocated (22 GB); the host volume has about 65 GB free.

## Template status

- The template for the pinned runtime exists, per the host's own record: `skill-runtime`
  `sha256:d9ec93ae65b0...fed` is registered as template **`sbam-rt-d9ec93ae65b0...`** (READY, per
  `COLLEAGUE-env.txt`). `monitor/monitor.log` (2026-11-11 13:59 to 15:29) records **9** `create_sandbox: success`
  entries, every one with `tpl=sbam-rt-d9ec93ae65b085`, so a sandbox has been created from it on this setup.
  A second image, `sha256:bb83fcd1dd3c...`, has its own alias (`template-build/alias.txt`).
- **Confirm after boot:** `g.sh "/usr/local/bin/cubemastercli tpl ls"` must list the alias as READY. The templates
  live on the guest's disk, so they survive a power-off.
- **Rebuild path** if it is missing: `add-runtime-template.sh <skill-runtime.tar>` (loads the image, builds
  `template-build/Dockerfile`, pushes it to `localhost:5000`, runs `cubemastercli tpl create-from-image ... --alias
  sbam-rt-<56 hex> --writable-layer-size 2Gi --expose-port 49983 --probe /health --cpu 1000 --memory 1024`), or
  `scripts/sandbox-template.sh` from a checkout. The template adds `envd`, the entrypoint, `/opt/sbam/harness`
  (root-owned, read-only), `/etc/sbam-runtime` (the full digest) and a root-owned `/work` for uid 10001. The adapter
  reads `/etc/sbam-runtime` on every run and refuses a mismatch (`TEMPLATE_DIGEST_MISMATCH`).

## Safety checks and test cases (E0.10-T01..T08)

Run on 2026-11-19 with the bring-up above (guest 4096 MB / 2 vCPU, no `relay.sh`), driving the Cube SDK from a
script that reuses the `smoke/` calls (`sdk.Sandbox.create(alias, allow_internet_access=False, network={"allow_out": [...]})`,
`box.commands.run`, `box.kill`). Egress evidence is the guest collector's `query.py <vm_id>`. Sandboxes are
1 vCPU / 1024 MB (the VM reports `MemTotal 995272 kB`, `nproc 1`).

| ID | Result | Verdict |
|---|---|---|
| T01 Template from image | `cubemastercli tpl ls`: `tpl-2b378bae8e574a5891dc1bfb`, alias `sbam-rt-d9ec93ae65b08532063f23cd9b855aa559debbe881ff0f4fa85f19fd`, READY (created 2026-11-11). A sandbox from it reads `/etc/sbam-runtime` = `sha256:d9ec93ae65b08532063f23cd9b855aa559debbe881ff0f4fa85f19fd4fa59fed`, which matches the pinned digest. The tests run as `uid=10001(sbam)`, Python 3.12.15, `/opt/sbam/harness` present. The template was not rebuilt in this run. | **Pass** |
| T02 FastMCP echo tool | FastMCP 4.0.10 is in `/opt/venv` (not the system python). A FastMCP server with an `echo` tool and an in-VM `fastmcp.Client` returned `RESULT hello-cube`, exit 0 (VM `921f20d2f553...`). | **Pass** |
| T03 Egress blocked | Empty allowlist, `allow_internet_access=False`. A literal-IP connection to `172.66.157.237:443` (example.org) and to `1.1.1.1:8443` was refused in 61 ms and 55 ms (`ConnectionRefusedError`). Collector lines: `"dst_host":"172.66.157.237","dst_port":443,"verdict":"BLOCK","source":"HOST_TAP","reason":"RST_AT_GATEWAY"` and the same for `1.1.1.1:8443` (VM `7cf11be84788...`, 2026-11-19T09:59:31Z). `https://example.org` by name failed with `Temporary failure in name resolution` after 10.5 s; the two DNS queries are logged `"verdict":"UNKNOWN","reason":"DNS_QUERY"`. **No name was logged BLOCK**: that is how the collector is designed (DNS and UDP are UNKNOWN), and the block is shown by the literal-IP connections. | **Pass** |
| T04 Egress allowed | `allow_out ["10.0.2.2/32"]`; the tool's GET to `http://10.0.2.2:18099/` (a host `http.server`, bound to `127.0.0.1`: QEMU user-net maps the guest's 10.0.2.2 to host loopback) returned `HTTP 200`. Collector: `"dst_host":"10.0.2.2","dst_port":18099,"verdict":"ALLOW","source":"HOST_TAP","reason":null` (VM `594aac7c78f8...`). The target is a host mock, not `sbam-mock-api` (it is not host-published). | **Pass** |
| T05 Start latency | 20 sequential create -> first command -> kill: see the table below. **create p50 121.5 ms, p95 135.2 ms; create + first command p50 201.0 ms, p95 211.1 ms**; kill 80-105 ms; every run exit 0, no sandbox left over. p95 is about 0.2 s against a 10 minute target. | **Pass** |
| T06 Limits | VM `479ab0b279dc...`. A 2 GB allocation raised `MemoryError` after 91 ms (an OOM-class failure: Python got `MemoryError`, not a kernel OOM kill) and the VM still answered afterwards. `sleep 120` with a 20 s client timeout ended with `RuntimeError: deadline_exceeded: context deadline exceeded` after 20 s. The VM was then destroyed (`/sandboxes` = `[]`). **The adapter's 60 s case was not exercised** (the timeout here was shortened to 20 s). | **Pass for memory; timeout shown at 20 s only** |
| T07 Public exposure | **N/A for Netbird** (the owner does not use it). Substitute: `ss -ltn` on the host after boot and after all tests, identical (`/tmp/t07_ss_ltn.txt`, `/tmp/t07_ss_ltn_after_tests.txt`): `127.0.0.1:13000`, `:11443`, `:12088`, `:10022`, `:18080` listen; nothing on `0.0.0.0`; 23000, 28080, 22088 and 29000 do not listen; `relay.sh` was never run. This is loopback evidence only, not an nmap from outside. | **Substitute holds** |
| T08 Isolation between runs | Run 1 (VM `3f47a0505b32...`) wrote `/tmp/marker` (`run1-secret`) and `/work/out/m`, then was destroyed. Run 2 (VM `b4e73df9c39c...`): `ls: cannot access '/tmp/marker'`, `/work/out/m` also absent, `test -e /tmp/marker` printed `ABSENT`. | **Pass** |

T05 raw numbers, in milliseconds (run 1 to 20). Method: `time.perf_counter()` around `sdk.Sandbox.create(...)`
(it returns once the template's `/health` probe on 49983 passes), then around a `true` command; percentiles with
`statistics.quantiles(..., n=100, method="inclusive")`; a warm template, a single guest.

| | Values |
|---|---|
| create | 121, 123, 111, 115, 112, 114, 129, 127, 135, 122, 128, 139, 119, 112, 116, 112, 124, 117, 126, 129 |
| create + first command | 206, 196, 186, 192, 192, 194, 201, 199, 209, 201, 211, 213, 196, 186, 205, 192, 206, 202, 207, 203 |

Boot of the guest to a healthy CubeAPI took about 40 s (4 polls, 10 s apart); `systemctl --failed` listed no units.
At the end the guest showed about 2.5 GB used of 3.6 GB, so 4 GB is the right size for it.

### Caveats

- **One guest, one warm template, loopback only.** Latency is for an already-registered template on a single guest;
  it says nothing about a cold template, concurrency, or a production host.
- **Netbird is replaced by a loopback substitute** (T07); no scan from outside was run.
- **Per-run CPU and RAM were not measured** beyond the configured size (1 vCPU, 1024 MB).
- **The adapter's 60 s timeout case was not exercised** in T06 (20 s client timeout used).
- **DNS verdicts are UNKNOWN by the collector's design**; blocking is evidenced by literal-IP connections.
- T04 used a host-side mock bound to loopback, not `sbam-mock-api`.

Edge cases from the issue (OOM, timeout, host restart with a VM running): OOM and timeout are T06. For a host
restart the adapter ends the run as an infrastructure error (never a correction), and a sandbox that survived is
destroyed by the next run's cleanup; this is not exercised yet.

Egress evidence limits, from `egress/CONTRACT-for-colleague.txt`: only `BLOCK` fails a run (`EGRESS_BLOCKED`).
A destination that refuses in under 5 ms (a closed port on the LAN) looks like Cube's own drop and is reported
`BLOCK`, which errs on the side of not certifying. UDP and DNS are `UNKNOWN`. A missing, partial or unreachable
collector answer is `EGRESS_LOG_UNAVAILABLE`, never an empty list.

## Findings

- The architecture and bring-up above work as written: one command boots the guest, CubeAPI answers on
  `127.0.0.1:13000` after about 40 s, and every `cube*` unit is up (`systemctl --failed` empty; the templatecenter
  was still "activating" at the first look, and nothing needed it).
- Template-from-image is confirmed live: the READY template for the pinned digest creates a sandbox whose
  `/etc/sbam-runtime` carries that digest, running as uid 10001.
- Egress default-deny is enforced and logged: literal-IP connections are dropped at the gateway and recorded as
  `BLOCK` / `RST_AT_GATEWAY`; the allowlisted address answers 200 and is recorded `ALLOW`.
- Sandboxes start in about 0.12 s and are ready for a command in about 0.2 s; state does not leak between runs;
  the memory limit holds.
- Impact on the host stayed small and reversible: one QEMU process, loopback forwards, a clean `poweroff` at the end.

## Recommendation

**Template-from-image is CONFIRMED, and the egress, isolation and latency acceptance of #28 is MET on a Linux/KVM
host** (T01-T06 and T08 above; T07 by the loopback substitute). No ADR is raised: template-from-image stands.
`#122` (a production-grade dedicated host and CI automation of these cases) remains the follow-up, together with the
caveats above: cold-template and concurrent start latency, the adapter's 60 s timeout case, per-run CPU/RAM, and a
real `sbam-mock-api` allow-target. `compose/sandbox-host/INSTALL.md` is **not** created: it is superseded by
`docs/runbooks/sandbox-host.md` and this report.
