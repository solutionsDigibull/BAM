#!/usr/bin/env bash
# Retention on the BACKUP HOST (E16.5, docs/runbooks/backup-restore.md); the VMs cannot prune
# (rest-server --append-only). Daily cron:
#
#   scripts/backup-prune.sh sbam-dev sbam-staging sbam-prod
#
# Keeps every set of the last 30 days and always the newest one (`--keep-last 1`), so a VM whose
# backups have been failing for weeks still has its last good set: backup.sh uploads complete,
# verified sets only, so the newest snapshot is always a good one (E16.5-T06). Grouped by host (one
# per environment, RESTIC_HOST in backup.sh): each set has its own path, and restic's default
# grouping by path would make every set a group of one that `--keep-last 1` never prunes.
# Env: BACKUP_DATA_DIR (/srv/sbam-backup, the rest-server --path), BACKUP_KEYS_DIR
# (/etc/sbam-backup/keys: <repo>.password = that environment's restic key, root 0400).
set -euo pipefail

RESTIC_IMAGE="restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
data="${BACKUP_DATA_DIR:-/srv/sbam-backup}"
keys="${BACKUP_KEYS_DIR:-/etc/sbam-backup/keys}"
(($#)) || { echo "usage: backup-prune.sh <repo>..." >&2; exit 1; }

rc=0
for repo in "$@"; do
  [[ "$repo" =~ ^[a-z0-9-]+$ ]] || { echo "prune: bad repo name $repo" >&2; rc=1; continue; }
  echo "prune: $repo"
  docker run --rm --network none -v "$data/$repo:/repo" -v "$keys/$repo.password:/run/key:ro" \
    -e RESTIC_REPOSITORY=/repo -e RESTIC_PASSWORD_FILE=/run/key "$RESTIC_IMAGE" \
    forget --group-by host --keep-within 30d --keep-last 1 --prune || rc=1
done
exit "$rc"
