# Monthly restore drill (E16.5)

A backup counts only once it has been restored. Once a month, and after any change to the backup or the database layout, restore the latest **prod** set onto a clean, isolated VM. Check that everything comes back, and measure RPO and RTO. Targets: **RPO ≤ 24 h, RTO ≤ 8 h, 100% of frozen artifacts verify.** For how backups are made, see the backups runbook (`backup-restore.md`).

## Before you start

- **A clean VM** with Docker, Netbird (any group that reaches `backup-host`) and this repo checked out at the release that was running in prod. The drill refuses to run if the VM already has SBAM data (`sbam_pgdata`). It never restores over a stack.
- **Isolation:** the drill VM must not reach production. Put the prod endpoints in `DRILL_FORBIDDEN_HOSTS` (for example `vm-prod.netbird:22 vm-prod.netbird:443 portal.example.com:443`). If any of them answers, the drill refuses to start. The stack runs the dev overlay with the mock LLM, so nothing calls out.
- **An env file for the drill**, a copy of `compose/.env.example` filled in: `SBAM_ENV=development` and fresh passwords. These are new values, not prod's.
- **Prod's release file**, the `release.env` from `/opt/sbam/releases/<current>/` on the prod VM (it holds digests only, no secrets).
- **Read access to the prod repository:**
  - `/etc/sbam/secrets/backup_repo_password` holds the prod restic key, from the offline copy;
  - `backup_rest_password` holds the `sbam-prod` login;
  - set `BACKUP_HOST`, `BACKUP_USER=sbam-prod` and `BACKUP_CACERT`.

## Run it

```bash
DRILL_FORBIDDEN_HOSTS="vm-prod.netbird:22 vm-prod.netbird:443" SMOKE_URLS="https://localhost/" \
  scripts/restore-drill.sh --env-file drill.env --release release-prod.env --bundle . --set latest
```

What it does:
1. **Isolation check:** refuses to run if SBAM data exists here or any forbidden host answers.
2. **Restore the set:** `restic restore`s the set, then checks every file against the set's `manifest.json`.
3. **Databases and volumes:**
   - starts Postgres on a fresh volume (`init.sh` creates the roles and empty databases);
   - `docker compose create` creates the volumes with their labels;
   - untars `storage` and `cognee_data`;
   - runs `pg_restore --clean --if-exists --exit-on-error` on the 5 databases.
4. **Start the stack** with `scripts/deploy.sh` (`--env dev`, id `drill-<set>`). The migration is a no-op at the same version, and the health watch runs as on a deploy. Temporal picks up the workflows that were running at backup time; their activities are idempotent.
5. **Hash check:** runs `python -m sbam_api.artifacts.verify` inside sbam-api to re-hash every **frozen** artifact against the sha256 recorded when it was stored, then `--all` for every artifact. Any mismatch or missing object fails the drill.
6. **Smoke check:** probes `SMOKE_URLS`.
7. **Report:** writes `/var/lib/sbam-drill/drill-<time>.json`:
   - `rpo_seconds` = failure time (`--failure-at`, default the drill start) − end of the set;
   - `rto_seconds` = drill start → smoke green;
   - both verifier reports;
   - `PASS` or `FAIL`. The script exits non-zero on FAIL.

## Record it (E16.5-T02)

Paste the report JSON and the `PASS`/`FAIL` line into the month's drill issue or the release PR. Include the date, set id, RPO, RTO and frozen counts.

Then wipe the drill VM (`docker compose down -v` there), since it holds a full copy of production data.

**If a drill fails:**
- **RPO:** look at the backups (backups runbook, "When a backup failed").
- **Hash mismatch:** the stored object differs from what was frozen. Escalate as a data-integrity incident. Check the original on prod with the same verifier before anything else.
- **RTO:** time each step from the log and fix the slowest one.
