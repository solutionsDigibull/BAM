# Spike E0.10 (#28): CubeSandbox on a Linux/KVM host

> **Status: bring-up documented from the source and the existing host files; NOT executed for this report.**
> No live measurement is in this document. T01-T06 and T08 below are procedures, not results. The live run
> waits for a host with at least 4 GB of free RAM (see Resources).

## Question

Can SBAM run a generated tool in a CubeSandbox VM built from a pinned `skill-runtime` image: a template made
from the image, a FastMCP tool run inside it, an egress default-deny that is enforced and logged, a start
latency compatible with the 10 minute Sub-Skill target, and no state shared between runs?

## Setup

What this report is based on (read, not run, on 2026-11-19):

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

## Bring-up (verified by reading, not run)

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
- **Not run.** Nothing above was executed for this report. The host was not started.

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
  A live run therefore needs headroom: stop unrelated containers first, or use another host. Booting the guest
  here risks an out-of-memory kill of the SBAM stack that the testers use.
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

"Procedure documented" means the steps are written here and have not been run; the live measurement is pending a
host with at least 4 GB free.

| ID | Design / expected | Status |
|---|---|---|
| T01 Template from image | The template alias carries the first 56 hex of the image digest, and `/etc/sbam-runtime` holds the full digest. Check: `cubemastercli tpl ls`, then create a sandbox from the alias and read `/etc/sbam-runtime`. | Evidence by record only: template READY and 9 creates in `monitor.log`. Procedure documented; live re-check pending. |
| T02 FastMCP echo tool | Start a sandbox from the template, call an echo tool with the MCP client inside it, expect the right output and exit 0 (`smoke/e2e.py` and `smoke/smoke.py` are the starting points). | Procedure documented; live run pending. |
| T03 Egress blocked | `allow_internet_access=False` with an empty `allow_out`; the tool requests `https://example.org`. Cube drops it at L3/L4; the host collector reports `BLOCK` (`RST_AT_GATEWAY`: a SYN answered by an RST within 5 ms on the sandbox tap). | Procedure documented; live run pending. |
| T04 Egress allowed | `allow_out` holds the `sbam-mock-api` address (`/32`); the tool gets HTTP 200 and the collector reports `ALLOW` (SYN answered by SYN-ACK). `sbam-mock-api` must be reachable from the guest (a host-published port through `10.0.2.2`; to be checked). | Procedure documented; live run pending. |
| T05 Start latency | 20 sequential sandbox creates from the template, p50 and p95 of create-to-ready (`/health` probe on 49983). Compare p95 with the 10 minute target. | Procedure documented; live run pending. |
| T06 Limits | One tool allocates 2 GB (expect an OOM category) and one sleeps 120 s (expect TIMEOUT); the VM is destroyed afterwards. The template fixes size (1 vCPU, 1024 MB); the adapter reads the VM size after creating it and destroys a VM above the run's limits. | Procedure documented; live run pending. |
| T07 Public exposure | **N/A: the owner does not use Netbird**, so the Netbird-group controls (#28, #122) do not apply. The substitute is: QEMU forwards bind `127.0.0.1` only, the guest's CubeNet drops, and `relay.sh` is not run. Evidence method: record `ss -ltn` on the host before and after boot and show that none of 13000, 18080, 12088, 11443, 10022 listens on a non-loopback address. | N/A for Netbird. Substitute documented; `ss -ltn` evidence pending a live boot. |
| T08 Isolation between runs | Run 1 writes `/tmp/marker`; run 2, from a new sandbox of the same template, must not find it. Each run's VM is destroyed afterwards (`kill_sandbox`). | Procedure documented; live run pending. |

Edge cases from the issue (OOM, timeout, host restart with a VM running): OOM and timeout are T06. For a host
restart the adapter ends the run as an infrastructure error (never a correction), and a sandbox that survived is
destroyed by the next run's cleanup; this is not exercised yet.

Egress evidence limits, from `egress/CONTRACT-for-colleague.txt`: only `BLOCK` fails a run (`EGRESS_BLOCKED`).
A destination that refuses in under 5 ms (a closed port on the LAN) looks like Cube's own drop and is reported
`BLOCK`, which errs on the side of not certifying. UDP and DNS are `UNKNOWN`. A missing, partial or unreachable
collector answer is `EGRESS_LOG_UNAVAILABLE`, never an empty list.

## Findings

No measurements. What this review established:

- The architecture and the bring-up above, from the source and the host files.
- The system impact is small and reversible, with one exception (`relay.sh`), which is avoided.
- The template-from-image mechanism is demonstrated by existing records: a READY template for the pinned digest and
  nine successful creates from it.
- This host cannot run the guest safely right now (about 3 GB free).

## Recommendation

**Template-from-image is confirmed by the existing READY template and the `monitor.log` evidence** (9 sandboxes
created from `sbam-rt-d9ec93ae65b085`). The **full measurement run (T02-T06, T08, and the `ss -ltn` evidence for
T07) is deferred to a dedicated or headroom-rich sandbox host** with at least 4 GB free for the guest, using the
bring-up above with `relay.sh` left off. Until then #28 has its written result and install notes but not its
measurements, and the acceptance criterion "numbers recorded with method" stays open.

Open items for the follow-up: record p50/p95 over 20 runs; check how the guest reaches `sbam-mock-api`; confirm
the guest services start on boot; decide whether `compose/sandbox-host/INSTALL.md` (named in the issue) is still
wanted. **Decision: it is not created.** `compose/sandbox-host/INSTALL.md` is superseded by `docs/runbooks/sandbox-host.md` and this report, which carry the install notes. No ADR is raised:
template-from-image stands.
