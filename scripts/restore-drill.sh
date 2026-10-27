#!/usr/bin/env bash
# Monthly restore drill (E16.5, TD §20.6, docs/runbooks/backup-restore.md). On a CLEAN, isolated VM:
#
#   scripts/restore-drill.sh --env-file <drill.env> --release <release.env> --bundle <checkout>
#                            [--set <set id>|latest] [--failure-at <UTC ISO time>]
#
#   1. refuse unless isolated: no SBAM volumes here yet, and none of DRILL_FORBIDDEN_HOSTS
#      ("host:port ...", the production endpoints) answers
#   2. restore the backup set from the backup host and check every file against its manifest
#   3. postgres on fresh volumes -> pg_restore the 5 databases; untar storage + cognee_data
#   4. start the stack with the release (scripts/deploy.sh: migrate = no-op, health watch);
#      Temporal resumes what was in flight at backup time
#   5. re-hash every frozen artifact (python -m sbam_api.artifacts.verify, then --all), smoke-check
#   6. report: RPO = failure time (default: now) - end of the set, RTO = start -> smoke green, hash
#      counts; PASS only when RPO <= 24 h, RTO <= 8 h and 100% of frozen artifacts verify.
# The drill runs the dev overlay (mock LLM): it calls nothing outside the VM.
# Env: BACKUP_HOST, BACKUP_USER, BACKUP_CACERT, SBAM_SECRETS_DIR (backup_repo_password,
# backup_rest_password), DRILL_DIR (/var/lib/sbam-drill), DRILL_FORBIDDEN_HOSTS, SMOKE_URLS.
set -euo pipefail

RESTIC_IMAGE="restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
TAR_IMAGE="alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
DATABASES=(sbam temporal temporal_visibility keycloak langfuse)
VOLUMES=(storage cognee_data)
RPO_MAX=$((24 * 3600)) RTO_MAX=$((8 * 3600))

die() { printf 'drill: %s\n' "$*" >&2; exit 1; }
say() { printf 'drill: %s\n' "$*"; }
epoch() { date -u -d "$1" +%s; }

env_file="" release="" bundle="" set_id="latest" failure_at=""
while (($#)); do
  case "$1" in
    --env-file) env_file="$2"; shift 2 ;;
    --release) release="$2"; shift 2 ;;
    --bundle) bundle="$2"; shift 2 ;;
    --set) set_id="$2"; shift 2 ;;
    --failure-at) failure_at="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
[[ -f "$env_file" && -f "$release" && -f "$bundle/compose/compose.yaml" ]] ||
  die "--env-file, --release and --bundle (a checkout with compose/) are required"
[[ "$set_id" == latest || "$set_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "--set must be a set id or latest"
secrets="${SBAM_SECRETS_DIR:-/etc/sbam/secrets}"
drill="${DRILL_DIR:-/var/lib/sbam-drill}"
: "${BACKUP_HOST:?}" "${BACKUP_USER:?}"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
t0="$(epoch "$started")"

# --- 1. isolation ----------------------------------------------------------------------------------
if [[ -n "$(docker volume ls -q --filter name=sbam_pgdata)" ]]; then
  die "this VM already has SBAM data (volume sbam_pgdata): the drill only runs on a clean VM"
fi
for target in ${DRILL_FORBIDDEN_HOSTS:-}; do
  if timeout 3 bash -c "</dev/tcp/${target%:*}/${target##*:}" 2>/dev/null; then
    die "$target is reachable from here: the drill VM must be isolated from production"
  fi
done
[[ -n "${DRILL_FORBIDDEN_HOSTS:-}" ]] || say "warning: DRILL_FORBIDDEN_HOSTS is empty, isolation not probed"

# --- 2. the backup set -----------------------------------------------------------------------------
rm -rf "$drill/restore" && mkdir -p "$drill/restore"
selector=(latest)
[[ "$set_id" == latest ]] || selector=(--tag "set:$set_id" latest)
docker run --rm -v "$drill/restore:/restore" -v "$secrets/backup_repo_password:/run/repo_password:ro" \
  ${BACKUP_CACERT:+-v "$BACKUP_CACERT:/run/backup-ca.pem:ro"} \
  -e RESTIC_REPOSITORY="rest:https://$BACKUP_HOST/$BACKUP_USER/" -e RESTIC_PASSWORD_FILE=/run/repo_password \
  -e RESTIC_REST_USERNAME="$BACKUP_USER" -e RESTIC_REST_PASSWORD="$(cat "$secrets/backup_rest_password")" \
  "$RESTIC_IMAGE" ${BACKUP_CACERT:+--cacert /run/backup-ca.pem} \
  restore "${selector[@]}" --target /restore || die "restic restore failed"
manifest="$(find "$drill/restore" -name manifest.json | head -1)"
[[ -n "$manifest" ]] || die "the restored set has no manifest.json"
set_dir="$(dirname "$manifest")"
for f in "${DATABASES[@]/%/.dump}" "${VOLUMES[@]/%/.tar}"; do
  want="$(grep -o "\"$f\": {\"sha256\": \"[0-9a-f]*\"" "$manifest" | grep -o '[0-9a-f]\{64\}')" ||
    die "$f is not in the manifest"
  [[ "$(sha256sum "$set_dir/$f" | cut -d' ' -f1)" == "$want" ]] || die "$f does not match the manifest"
done
set_name="$(basename "$set_dir")"
set_end="$(grep -o '"finished": "[^"]*"' "$manifest" | head -1 | cut -d'"' -f4)"
say "set $set_name restored and verified against its manifest (ended $set_end)"

# --- 3. databases and volumes ----------------------------------------------------------------------
compose() {
  docker compose --env-file "$env_file" --env-file "$release" -f "$bundle/compose/compose.yaml" \
    -f "$bundle/compose/compose.deploy.yaml" -f "$bundle/compose/compose.dev.yaml" "$@"
}
compose up -d --wait postgres # a fresh volume: init.sh creates the roles and empty databases
compose create # every volume, with its Compose labels, before anything writes to it
for vol in "${VOLUMES[@]}"; do
  docker run --rm -i --network none -v "sbam_$vol:/v" "$TAR_IMAGE" tar -C /v -xf - <"$set_dir/$vol.tar" ||
    die "restoring volume sbam_$vol"
done
for db in "${DATABASES[@]}"; do
  compose exec -T postgres pg_restore -U postgres -d "$db" --clean --if-exists --exit-on-error \
    <"$set_dir/$db.dump" || die "pg_restore $db"
done

# --- 4. start, 5. verify and smoke ----------------------------------------------------------------
SBAM_RELEASES="$drill/releases" SBAM_ENV_FILE="$env_file" WATCH_CHECKS="${WATCH_CHECKS:-6}" \
  bash "$bundle/scripts/deploy.sh" --env dev --id "drill-$set_name" --release "$release" \
  --bundle "$bundle" || die "the restored stack did not become healthy"
frozen="$(compose exec -T sbam-api python -m sbam_api.artifacts.verify)" && frozen_ok=1 || frozen_ok=0
every="$(compose exec -T sbam-api python -m sbam_api.artifacts.verify --all)" && all_ok=1 || all_ok=0
smoke_ok=1
for url in ${SMOKE_URLS:-}; do
  code="$(curl -ksS -o /dev/null -w '%{http_code}' -m 20 "$url" || echo 000)"
  [[ "$code" =~ ^[23] ]] || { say "smoke $url: $code"; smoke_ok=0; }
done
green="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- 6. report -------------------------------------------------------------------------------------
rpo=$(($(epoch "${failure_at:-$started}") - $(epoch "$set_end")))
rto=$(($(epoch "$green") - t0))
result=PASS
((rpo <= RPO_MAX && rto <= RTO_MAX && frozen_ok && all_ok && smoke_ok)) || result=FAIL
report="$drill/drill-$(date -u +%Y%m%dT%H%M%SZ).json"
printf '{"result": "%s", "set": "%s", "set_end": "%s", "started": "%s", "smoke_green": "%s", "rpo_seconds": %s, "rto_seconds": %s, "rpo_ok": %s, "rto_ok": %s, "smoke_ok": %s, "frozen": %s, "all": %s}\n' \
  "$result" "$set_name" "$set_end" "$started" "$green" "$rpo" "$rto" \
  "$( ((rpo <= RPO_MAX)) && echo true || echo false)" "$( ((rto <= RTO_MAX)) && echo true || echo false)" \
  "$( ((smoke_ok)) && echo true || echo false)" "${frozen:-null}" "${every:-null}" >"$report"
cat "$report"
say "$result: RPO $((rpo / 60)) min (max 24 h), RTO $((rto / 60)) min (max 8 h), report $report"
[[ "$result" == PASS ]]
