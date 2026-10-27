#!/usr/bin/env bash
# Nightly backup of one VM (E16.5, TD §20.6, docs/runbooks/backup-restore.md). Run by the
# sbam-backup.timer (compose/backup/) as root or a docker-group user:
#
#   scripts/backup.sh --env dev|staging|prod
#
# One backup SET = a pg_dump (custom format, a consistent snapshot even under writes) of each
# database, a tar of the storage and cognee_data volumes, and manifest.json with the sha256 and size
# of every file. The set goes to this environment's restic repository on the backup host
# (rest-server --append-only: restic encrypts it, and this VM's credential can add snapshots but
# never forget or prune them; only the backup host prunes, scripts/backup-prune.sh). After the upload
# every file is read back from the repository and re-hashed against the manifest.
# Any failure (one dump, one tar, the upload, a hash) fails the whole set: nothing partial is kept,
# the previous sets are untouched, the attempt is logged and SBAM_ALERT_WEBHOOK_URL is alerted.
#
# Env: SBAM_PROJECT (sbam), BACKUP_HOST (backup host:port over Netbird), BACKUP_USER (the rest-server
# user = repo name, e.g. sbam-staging), BACKUP_STATE (/var/lib/sbam-backup), SBAM_SECRETS_DIR
# (/etc/sbam/secrets: backup_repo_password = restic key, backup_rest_password = rest-server login),
# BACKUP_CACERT (the backup host's CA, for its internal TLS certificate), SBAM_ALERT_WEBHOOK_URL.
set -euo pipefail

RESTIC_IMAGE="restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
TAR_IMAGE="alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
DATABASES=(sbam temporal temporal_visibility keycloak langfuse)
VOLUMES=(storage cognee_data)

die() { printf 'backup: %s\n' "$*" >&2; exit 1; }
say() { printf 'backup: %s\n' "$*"; }

env_name=""
while (($#)); do
  case "$1" in
    --env) env_name="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
case "$env_name" in dev | staging | prod) ;; *) die "--env must be dev, staging or prod" ;; esac
project="${SBAM_PROJECT:-sbam}"
state="${BACKUP_STATE:-/var/lib/sbam-backup}"
secrets="${SBAM_SECRETS_DIR:-/etc/sbam/secrets}"
: "${BACKUP_HOST:?set the backup host:port}" "${BACKUP_USER:?set the rest-server user (e.g. sbam-$env_name)}"
for f in backup_repo_password backup_rest_password; do
  [[ -s "$secrets/$f" ]] || die "secret $f is missing or empty in $secrets"
done

set_id="$(date -u +%Y%m%dT%H%M%SZ)"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
work="$state/work"
dir="$work/$set_id"
mkdir -p "$state"
exec 9>"$state/.lock"
if command -v flock >/dev/null; then flock -n 9 || die "another backup is running"; fi
rm -rf "$work" && mkdir -p "$dir"
trap 'rm -rf "$work"' EXIT

record() { # <result> [reason]
  printf '{"set": "%s", "env": "%s", "result": "%s", "reason": "%s", "started": "%s", "finished": "%s"}\n' \
    "$set_id" "$env_name" "$1" "${2:-}" "$started" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$state/log.jsonl"
}
failed() { # <reason>: the whole set fails, nothing partial is kept
  record FAILED "$1"
  if [[ -n "${SBAM_ALERT_WEBHOOK_URL:-}" ]]; then
    curl -fsS -m 10 -H 'Content-Type: application/json' \
      -d "{\"text\": \"SBAM $env_name backup $set_id FAILED: $1\"}" "$SBAM_ALERT_WEBHOOK_URL" >/dev/null ||
      say "alert webhook failed"
  fi
  die "set $set_id FAILED: $1 (previous sets are untouched)"
}

# --- 1. dumps and volume snapshots -----------------------------------------------------------------
pg="$(docker ps -q --filter "label=com.docker.compose.project=$project" \
  --filter label=com.docker.compose.service=postgres)"
[[ -n "$pg" ]] || failed "postgres container of project $project is not running"
for db in "${DATABASES[@]}"; do
  docker exec -i "$pg" pg_dump -U postgres -Fc "$db" >"$dir/$db.dump" || failed "pg_dump $db"
done
for vol in "${VOLUMES[@]}"; do
  docker run --rm --network none -v "${project}_$vol:/v:ro" "$TAR_IMAGE" tar -C /v -cf - . \
    >"$dir/$vol.tar" || failed "snapshot of volume ${project}_$vol"
done

# --- 2. manifest -----------------------------------------------------------------------------------
files=()
for db in "${DATABASES[@]}"; do files+=("$db.dump"); done
for vol in "${VOLUMES[@]}"; do files+=("$vol.tar"); done
{
  printf '{"set": "%s", "env": "%s", "started": "%s", "finished": "%s", "files": {' \
    "$set_id" "$env_name" "$started" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sep=""
  for f in "${files[@]}"; do
    printf '%s"%s": {"sha256": "%s", "size": %s}' "$sep" "$f" \
      "$(sha256sum "$dir/$f" | cut -d' ' -f1)" "$(wc -c <"$dir/$f" | tr -d ' ')"
    sep=", "
  done
  printf '}}\n'
} >"$dir/manifest.json"

# --- 3. upload, then read every file back and re-hash it -------------------------------------------
restic() { # <restic args...>, with the work dir read-only at /data
  docker run --rm -i -v "$work:/data:ro" -v "$secrets/backup_repo_password:/run/repo_password:ro" \
    ${BACKUP_CACERT:+-v "$BACKUP_CACERT:/run/backup-ca.pem:ro"} \
    -e RESTIC_REPOSITORY="rest:https://$BACKUP_HOST/$BACKUP_USER/" -e RESTIC_PASSWORD_FILE=/run/repo_password \
    -e RESTIC_REST_USERNAME="$BACKUP_USER" -e RESTIC_REST_PASSWORD="$(cat "$secrets/backup_rest_password")" \
    -e RESTIC_HOST="sbam-$env_name" "$RESTIC_IMAGE" ${BACKUP_CACERT:+--cacert /run/backup-ca.pem} "$@"
}
restic backup --quiet --tag "sbam-$env_name" --tag "set:$set_id" "/data/$set_id" ||
  failed "restic backup"
for f in "${files[@]}" manifest.json; do
  want="$(sha256sum "$dir/$f" | cut -d' ' -f1)"
  got="$(restic dump --tag "set:$set_id" latest "/data/$set_id/$f" | sha256sum | cut -d' ' -f1)" ||
    failed "reading $f back from the repository"
  [[ "$got" == "$want" ]] || failed "$f in the repository does not match its sha256"
done

bytes="$(cat "$dir"/* | wc -c | tr -d ' ')"
record OK "${#files[@]} files, $bytes bytes, verified"
printf '%s\n' "$started" >"$state/last_success"
say "set $set_id uploaded and verified (${#files[@]} files, $bytes bytes)"
