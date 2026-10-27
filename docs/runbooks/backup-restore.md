# Backups and restore (E16.5)

Every VM backs itself up every night to an **off-host, encrypted, append-only** restic repository. A monthly restore drill proves the backups work; see [the restore drill](#monthly-restore-drill).

## What a backup set holds

One set per night, id `YYYYMMDDTHHMMSSZ` (UTC):

| File | What |
|---|---|
| `sbam.dump`, `temporal.dump`, `temporal_visibility.dump`, `keycloak.dump`, `langfuse.dump` | `pg_dump -Fc` of each database. Each dump is a consistent snapshot even while the stack writes |
| `storage.tar`, `cognee_data.tar` | tars of the `sbam_storage` and `sbam_cognee_data` volumes |
| `manifest.json` | set id, env, times, plus sha256 and size of every file |

`scripts/backup.sh` uploads the set with `restic backup`, then reads every file back from the repository and checks its sha256 against the manifest.

**A set is all or nothing.** If one dump, tar, the upload or a hash fails:
- the whole set is `FAILED` and nothing partial stays in the repository;
- the previous sets are untouched;
- the attempt is written to `/var/lib/sbam-backup/log.jsonl`;
- `SBAM_ALERT_WEBHOOK_URL` gets an alert.

`last_success` holds the start time of the last good set. Use it to check RPO.

## Where it goes

- **One backup host**, separate from every VM, runs `compose/backup/compose.backup-host.yaml`: a restic **rest-server** on its Netbird address (group `backup-host`, port 8000, `compose/netbird/policies.json`).
- **Encrypted:** each environment has its own restic repository (`sbam-dev`, `sbam-staging`, `sbam-prod`), encrypted by restic with that environment's key.
- **Write-only for the VMs:** the server runs with `--append-only --private-repos`. A VM's login only sees its own repository, and it can add and read snapshots but never forget, prune or delete them (rest-server answers `403`, `test_e16_5_backup_host.py`). A compromised VM can't destroy its backups.
- **Retention** runs only on the backup host, with `scripts/backup-prune.sh sbam-dev sbam-staging sbam-prod` in a daily cron. It keeps every set of the last 30 days and always the newest set (`--keep-last 1`). Failed sets are never uploaded, so the newest is always a good one: a VM whose backups failed for weeks still has its last good set.
- **Later:** S3 can replace rest-server, because restic speaks S3. Use a bucket with object lock, and give the VM a key that can put but not delete objects.

## Set up the backup host (once, platform)

1. **Netbird:** join the host to group `backup-host`. Only the VMs reach its port 8000.
2. **TLS:** create an internal certificate (CN = the host's Netbird name) as `/etc/sbam-backup/tls/{cert,key}.pem`. Keep the CA, because the VMs need it.
3. **Logins:** add one login per environment with `htpasswd -B -c /etc/sbam-backup/htpasswd sbam-staging`, then repeat for each environment.
4. **Start rest-server:** `BACKUP_BIND=<netbird ip> docker compose -f compose/backup/compose.backup-host.yaml up -d`.
5. **Repository keys:**
   - Create each environment's key with `openssl rand -hex 32`.
   - Store it as `/etc/sbam-backup/keys/<repo>.password` (root, `0400`). Prune needs it.
   - Keep a second copy **offline** (the password manager). Without the key a repository can't be restored.
6. **Init each repository** from the VM, the first time: `restic init` with the VM's login. Append-only allows `init`.
7. **Prune:** add the daily cron `scripts/backup-prune.sh sbam-dev sbam-staging sbam-prod`.

## Set up a VM (once per VM)

- **Secrets** (root, `0400`, like the other deploy secrets):
  - `/etc/sbam/secrets/backup_repo_password`: that environment's restic key;
  - `/etc/sbam/secrets/backup_rest_password`: its rest-server login.
- **`/etc/sbam/backup.env`:**
  - `BACKUP_ENV=staging`
  - `BACKUP_HOST=<backup host netbird name>:8000`
  - `BACKUP_USER=sbam-staging`
  - `BACKUP_CACERT=/etc/sbam/backup-ca.pem`
  - `SBAM_ALERT_WEBHOOK_URL=<operator webhook>`
- **Install:** copy `scripts/backup.sh` to `/opt/sbam/bin/backup.sh`, and `compose/backup/sbam-backup.{service,timer}` to `/etc/systemd/system/`. Then run `systemctl enable --now sbam-backup.timer`.
- **First run:** start it by hand with `systemctl start sbam-backup`. Check that `tail -1 /var/lib/sbam-backup/log.jsonl` shows `"result": "OK"` (T01, recorded in the release PR).

## When a backup failed

**Symptoms:**
- an alert "SBAM <env> backup <set> FAILED: <reason>";
- `log.jsonl` has a `FAILED` line;
- `last_success` is older than 24 h, which breaks the RPO target.

**Fix:** the reason names the step.

| Reason | Check |
|---|---|
| `pg_dump <db>` | Is Postgres up? `docker compose ps postgres` |
| `snapshot of volume …` | Does the volume exist? |
| `restic backup` / `reading … back` | The backup host, Netbird, the certificate, the login |
| `… does not match its sha256` | Corrupted in transit or on the backup host. Run `restic check` there |

**Verify:** run `systemctl start sbam-backup` again and check for `"result": "OK"`.

## Monthly restore drill

Built with the second part of E16.5: `scripts/restore-drill.sh` and `python -m sbam_api.artifacts.verify`. Until then, restore by hand from the latest set:
1. `restic restore`;
2. `pg_restore` each dump into a fresh Postgres;
3. untar the volumes into fresh volumes;
4. start the stack.
