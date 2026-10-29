# Prerequisites: access and hosts (E0.14)

Everything the team needs before the build phases: GitHub Actions + GHCR, the OpenRouter key, the KVM sandbox host, the shared dev VM and the Netbird network. Sources: TD §20.1, TD §20.5, PLAN §24.2. The KVM host should be ready by **week 4**. This page holds **no secrets**: only where they live and who can grant access.

## Items and owners

Fill in a name for each owner. "Platform" means the D1 lane, and "project owner" means the person who holds the OpenRouter account.

| Item | Owner | How to get access | Verify with |
|---|---|---|---|
| GitHub org `solutionsDigibull`, repo `BAM` | Platform: _name_ | Ask the owner to add you to the team with **Write**. Work from a fork. | `gh repo view solutionsDigibull/BAM` |
| GitHub Actions + GHCR | Platform: _name_ | Nothing to request. CI pushes with `GITHUB_TOKEN`; developers only pull. | T01 |
| OpenRouter API key | Project owner: _name_ | Developers never receive the key. It goes straight into the stores listed in [OpenRouter key](#openrouter-key). | T02 |
| KVM sandbox host (CubeSandbox) | Platform: _name_ | Netbird group `sandbox-host` membership is granted by the Netbird admin. No SSH from the internet. | T03, T04, `scripts/verify-host.sh sandbox-host` |
| Shared dev VM (≥ 32 GB RAM, Docker) | Platform: _name_ | Netbird group `dev-laptops` grants access, plus an SSH key added by the owner. | T04, T07, `scripts/verify-host.sh dev-vm` |
| Netbird network | Netbird admin: _name_ | The admin sends a setup key or invite and puts your peer in `dev-laptops`. | T05 |

## Least privilege

- **GHCR.** Only CI writes to GHCR. Jobs ask for `packages: write`, and only the push steps use it (`ci.yml` image-scan on `main`/tags, and `prereqs.yml`). Developers get **read** on the packages. No personal access token with `write:packages` is ever created.
- **Secrets.** Secrets live only in GitHub Actions secrets and in VM secret files. A VM secret file is mode `0600`, owned by the service user, under `/etc/sbam/secrets/`. Secrets never go in the repo, in `compose/.env.example` (keys stay empty there), in issue text or in chat. CI's `secret-scan` job runs gitleaks over the full history on every PR (T06).
- **Network.** The dev VM, staging and the sandbox host have **no public SSH** and no public ops ports. Only the production portals go through the public proxy. The database, Temporal, Keycloak admin and Grafana are reachable only over Netbird (TD §20.5).

## Netbird

One network. Groups and policies come from TD §20.5:

| Group | Members | Can reach |
|---|---|---|
| `dev-laptops` | developer laptops | `vm-dev` and `vm-staging` ops ports (SSH 22, Grafana, Temporal UI, Keycloak admin) |
| `vm-dev` | shared dev VM | `sandbox-host` sandbox API |
| `vm-staging` | staging VM | `sandbox-host` sandbox API |
| `vm-prod` | production VM(s) | `sandbox-host` sandbox API |
| `sandbox-host` | KVM server | nothing outbound except what CubeSandbox needs |
| _platform_ | platform developers only | `vm-prod` ops ports |

Production ops access is limited to the platform group. A laptop in `dev-laptops` must **not** reach the `vm-prod` ops ports (T05).

**Joining from Windows.** Install the Netbird Windows client (`winget install Netbird.Netbird`), not the client inside WSL2. WSL2 shares the Windows network stack, so `ssh`, `docker context` and browsers in WSL2 reach Netbird peers through the Windows client. Then run `netbird up --setup-key <key from admin>` in an elevated PowerShell. Check the connection with `netbird status`.

**Joining a Linux host.** Install Netbird from the official package, run `netbird up --setup-key <key>`, and have the admin put the peer in its group. Bind SSH to the Netbird address only (`ListenAddress <wt0 IP>` in `sshd_config`), or firewall port 22 to the `wt0` interface.

## OpenRouter key

- **Stores:**
  - GitHub Actions secret `OPENROUTER_API_KEY` (repo settings → Secrets and variables → Actions).
  - On each VM that runs Bifrost: `/etc/sbam/secrets/openrouter_api_key`, mode `0600`, owned by the `sbam` service user. It is never committed.
    - **Not wired yet.** Bifrost reads `OPENROUTER_API_KEY` from `compose/.env` today (`compose/compose.yaml`, bifrost block).
    - Reading this file as a Docker secret arrives with #46 (Bifrost) and the VM Compose overlays (`compose.dev.yaml` / `compose.staging.yaml`).
    - Until then, keep the key in the VM's `compose/.env`, with that file at mode `0600` and owned by `sbam`.
- **Scope.** Use a dedicated key for SBAM with a monthly **credit limit** set in OpenRouter. Use separate keys for dev/staging and production, so a leak in one doesn't affect the other.
- **Rotation (every 90 days, and whenever someone with access leaves):**
  1. Create the new key in OpenRouter.
  2. Update the Actions secret and every VM secret file (`install -m 600 -o sbam /dev/stdin /etc/sbam/secrets/openrouter_api_key`, then paste the key and press Ctrl-D).
  3. Restart Bifrost (`docker compose up -d bifrost`) and run `prereqs.yml` (T02).
  4. Revoke the old key in OpenRouter.
- **Leaked key drill.** Practise this once, and again after any real leak:
  1. Revoke the key in OpenRouter **first**.
  2. Rotate, following the steps above.
  3. Check OpenRouter usage for calls that weren't ours.
  4. If the key ever reached Git, tell the security owner. Revoking the key is the fix; never rewrite history just to hide the leak.
  5. Record the date and how long it took in the checklist below.

## Keycloak service account for tenants (E2.3)

`POST /admin/tenants` creates each tenant's Keycloak organization as the confidential client `sbam-api-admin`. The realm file stays free of secrets (E2.1-T08), so the client is created outside it:

- **Laptops and CI.** `keycloak-seed` creates it from `KEYCLOAK_ADMIN_CLIENT_SECRET` in `compose/.env`. With the variable empty, tenant creation answers 503 `IDP_UNAVAILABLE` and nothing else changes.
- **Dev VM, staging, prod.** Create it once in the `bullm` realm:
  1. Confidential client, service account on, every other flow off.
  2. Give its service account the `realm-management` role `manage-realm`. Keycloak 26.8 refuses the organizations API with `manage-users` alone.
  3. Put the secret in the sbam-api environment as `KEYCLOAK_ADMIN_CLIENT_SECRET`, with `KEYCLOAK_INTERNAL_URL` pointing at the realm inside the network.
- **Rotation.** Regenerate the secret in Keycloak, update sbam-api's environment, then restart sbam-api.

## Keycloak sign-in hardening (A-18, #438)

`compose/keycloak/render_realm.py` writes the realm file a deployed environment imports. Besides the public origin it sets the password policy and the second factor (`--mfa admins|all|off`, default `admins`); the committed dev realm has neither, so `keycloak-seed` users keep working. What an operator needs to know:

- **Admins enrol at their next sign-in.** A user holding `sbam-admin` or `super-admin` who has no authenticator is sent to set up TOTP right after the password. Tell them before the first deploy. A user who loses the device is reset in the Keycloak admin console (Users, Credentials, delete the OTP): they enrol again at the next sign-in. Keep a second `super-admin` with its own device so one lost phone never locks the operator out.
- **The realm is imported once.** `--import-realm` skips a realm that already exists, so a new `passwordPolicy`, flow or role reaches an environment only when its realm is created from the rendered file. For a running realm, apply the policy with `kcadm.sh update realms/bullm -s 'passwordPolicy=length(12) and passwordHistory(5) and notUsername and notEmail and passwordBlacklist(common-passwords.txt)'` and build the `browser-mfa` flow by copying the `browser` flow in the admin console (see the rendered file for the steps and the `mfa-required` role). Scripting this is #543.
- **The blacklist file and the policy travel together.** `compose.deploy.yaml` mounts `compose/keycloak/password-blacklist.txt` where Keycloak reads it. A policy that names a missing file rejects every password change, and a policy name Keycloak does not know stops the server from starting: change `PASSWORD_POLICY` only with `tests/integration/test_a18_keycloak_hardening.py` (it starts a throwaway Keycloak).
- **Events.** Keycloak stores login, password and credential events for 30 days and admin events with their representation (secrets are masked by Keycloak), and writes them to its log. Forwarding them into SBAM's audit log is #541.
- **WebAuthn is not offered yet.** A required TOTP step cannot sit next to an alternative WebAuthn step in one Keycloak flow level; that is #542.

## KVM host not ready (fallback)

If the sandbox host isn't ready by week 4, sandbox work continues on laptops with `make up PROFILES=sandbox-local`. That runs a `local-docker` sandbox (#42, #123). Runs from it are marked **non-certifying** and can never certify or freeze a version. Certification waits for the real CubeSandbox host (#28).

## Test cases (E0.14)

| ID | How | Expected | Result (date · who · evidence) |
|---|---|---|---|
| T01 GHCR push | Actions → `prereqs` → Run workflow; then each developer runs `docker pull ghcr.io/solutionsdigibull/sbam-prereq-hello:latest` | Image present; every developer can pull it | |
| T02 OpenRouter key | `prereqs` workflow, `openrouter` job | `OpenRouter HTTP 200`; the key is masked in the log | |
| T03 KVM ready | `kvm-ok` on the sandbox host (also run by `scripts/verify-host.sh sandbox-host`) | `KVM acceleration can be used` | |
| T04 Network exposure | `scripts/verify-host.sh` on each host, **and** from a machine outside Netbird `nmap -Pn -p- <public IP>` | No open ports, except the portal proxy where intended (prod only) | |
| T05 Netbird groups | From a `dev-laptops` peer: `ping <vm-dev>` and `nc -zv <vm-prod> 22` | vm-dev reachable; vm-prod ops port refused | |
| T06 No secrets committed | CI `secret-scan` (gitleaks, full history) on every PR | 0 findings | Automated in CI |
| T07 Secret file permissions | `stat -c '%a %U' /etc/sbam/secrets/openrouter_api_key` (also run by `verify-host.sh dev-vm`) | `600 sbam` | |

## Access checklist

Each developer ticks their own column once they have checked the item themselves, with the date. The issue (#27) closes when every cell is ticked.

| Item | D1 | D2 | D3 | D4 |
|---|---|---|---|---|
| GitHub repo (Write, fork) | | | | |
| `docker pull` from GHCR | | | | |
| Netbird joined (`netbird status` connected) | | | | |
| SSH to dev VM over Netbird | | | | |
| Dev VM: `make up` healthy | | | | |
| Sandbox host reachable from dev VM (platform only) | | | | |
