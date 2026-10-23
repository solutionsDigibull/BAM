#!/usr/bin/env bash
# Deploy one release to this VM, with automatic rollback (E16.4, TD §20.4, docs/runbooks/deploy.md).
# Runs as the VM's deploy user (docker group); .github/workflows/deploy.yml calls it via ci-deploy.sh.
#
#   scripts/deploy.sh --env dev|staging|prod --id <release id> --release <release.env> --bundle <dir>
#
# <dir> holds the release's compose/ folder (the tagged commit's); <release.env> its image digests
# (compose/release.env.example). Each release is kept in $SBAM_RELEASES/<id>/ and the file `current`
# names the one running, so a rollback redeploys the previous release with its own compose files.
#
#   1. validate the digests; the same digests as `current` = nothing to do (exit 0, nothing recreated)
#   2. pull, then run api-migrate with the new images: a failed migration stops here, the old
#      version keeps serving (exit 1). Migrations are expand/contract, so rolling the app images back
#      never needs a downgrade.
#   3. up -d --wait (workers drain on SIGTERM, compose.deploy.yaml stop_grace_period), then watch the
#      health for WATCH_CHECKS x WATCH_INTERVAL s (default 30 x 10 s = 5 min). Three bad checks in a
#      row (unhealthy, restarting, or a crashed container) = failed: redeploy the previous release,
#      log it, alert SBAM_ALERT_WEBHOOK_URL (exit 2).
# Never `down`, never `-v`, never a volume command: data volumes and frozen artifacts are not touched.
# Env: SBAM_RELEASES (/opt/sbam/releases), SBAM_ENV_FILE (/etc/sbam/<env>.env), SBAM_PROFILES
# (e.g. obs,analytics), SBAM_DEPLOY_ACTOR (who approved/started it), SBAM_ALERT_WEBHOOK_URL.
set -euo pipefail

ROOT="${SBAM_RELEASES:-/opt/sbam/releases}"
WATCH_CHECKS="${WATCH_CHECKS:-30}"
WATCH_INTERVAL="${WATCH_INTERVAL:-10}"
BAD_IN_A_ROW=3

die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
say() { printf 'deploy: %s\n' "$*"; }

env_name="" id="" release="" bundle=""
while (($#)); do
  case "$1" in
    --env) env_name="$2"; shift 2 ;;
    --id) id="$2"; shift 2 ;;
    --release) release="$2"; shift 2 ;;
    --bundle) bundle="$2"; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
case "$env_name" in dev | staging | prod) ;; *) die "--env must be dev, staging or prod" ;; esac
[[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || die "--id must be a tag or commit (letters, digits, . _ -)"
[[ -f "$release" ]] || die "--release file not found"
[[ -f "$bundle/compose/compose.yaml" ]] || die "--bundle has no compose/compose.yaml"
ENV_FILE="${SBAM_ENV_FILE:-/etc/sbam/$env_name.env}"
[[ -f "$ENV_FILE" ]] || die "environment file $ENV_FILE not found"

# The image lines of a release file, sorted: what "the same release" means.
digests() { grep -E '^SBAM_[A-Z_]+_IMAGE=' "$1" | sort; }

images="$(digests "$release")"
[[ -n "$images" ]] || die "no SBAM_*_IMAGE lines in the release file"
while IFS= read -r line; do
  [[ "$line" =~ =[a-z0-9./_-]+@sha256:[0-9a-f]{64}$ ]] || die "${line%%=*} is not an image@sha256 digest"
  [[ "$line" == *@sha256:0000000000000000000000000000000000000000000000000000000000000000 ]] &&
    die "${line%%=*} is still the template's placeholder digest"
done <<<"$images"

mkdir -p "$ROOT"
exec 9>"$ROOT/.lock"
if command -v flock >/dev/null; then flock -n 9 || die "another deploy is running"; fi

previous="$(cat "$ROOT/current" 2>/dev/null || true)"
if [[ -n "$previous" && -f "$ROOT/$previous/release.env" &&
  "$(digests "$ROOT/$previous/release.env")" == "$images" ]]; then
  say "release $id: these digests are already running ($previous); nothing to do"
  exit 0
fi

dir="$ROOT/$id"
if [[ -f "$dir/release.env" && "$(digests "$dir/release.env")" != "$images" ]]; then
  die "release $id was deployed before with other digests; use a new id"
fi
mkdir -p "$dir"
rm -rf "$dir/compose"
cp -R "$bundle/compose" "$dir/compose"
cp "$release" "$dir/release.env"

compose() { # <release dir> <compose args...>: the overlay order of compose.deploy.yaml
  local d=$1 p
  shift
  local args=(--env-file "$ENV_FILE" --env-file "$d/release.env"
    -f "$d/compose/compose.yaml" -f "$d/compose/compose.deploy.yaml")
  for p in ${SBAM_PROFILES:+${SBAM_PROFILES//,/ }}; do
    args+=(-f "$d/compose/compose.$p.yaml" -f "$d/compose/compose.deploy.$p.yaml" --profile "$p")
  done
  args+=(-f "$d/compose/compose.$env_name.yaml")
  docker compose "${args[@]}" "$@"
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
started="$(now)"
record() { # <result>: one JSON line per deploy attempt
  local list
  list="$(sed -E 's/^([A-Z_]+)=(.*)$/"\1": "\2"/' <<<"$images" | paste -sd, -)"
  printf '{"release": "%s", "env": "%s", "result": "%s", "previous": "%s", "actor": "%s", "started": "%s", "finished": "%s", "images": {%s}}\n' \
    "$id" "$env_name" "$1" "$previous" "${SBAM_DEPLOY_ACTOR:-unknown}" "$started" "$(now)" "$list" \
    >>"$ROOT/log.jsonl"
}
alert() {
  [[ -n "${SBAM_ALERT_WEBHOOK_URL:-}" ]] || return 0
  curl -fsS -m 10 -H 'Content-Type: application/json' \
    -d "{\"text\": \"SBAM $env_name: $1\"}" "$SBAM_ALERT_WEBHOOK_URL" >/dev/null ||
    say "alert webhook failed"
}

# One health check: any service unhealthy or restarting, or a container that exited non-zero (one-off
# jobs like api-migrate exit 0 and are fine).
healthy() {
  local out
  out="$(compose "$dir" ps -a --format '{{.Service}} {{.State}} {{.Health}} {{.ExitCode}}')" || return 1
  # shellcheck disable=SC2016 # awk's $fields, not the shell's
  local rule='$3 == "unhealthy" || $2 == "restarting" || $2 == "dead" || ($2 == "exited" && $NF != "0")'
  ! awk "$rule"' { print "deploy: unhealthy " $1 " (" $2 " " $3 ")"; n++ } END { exit !n }' <<<"$out"
}

say "release $id to $env_name (previous: ${previous:-none})"
compose "$dir" pull --quiet
if ! compose "$dir" run --rm --no-deps api-migrate | tee "$dir/migrate.log"; then
  record migration_failed
  alert "release $id: migration failed, nothing rolled out (still on ${previous:-nothing})"
  die "api-migrate failed; the running version is unchanged"
fi

ok=1
if compose "$dir" up -d --wait --wait-timeout 300; then
  bad=0
  for ((i = 0; i < WATCH_CHECKS; i++)); do
    sleep "$WATCH_INTERVAL"
    if healthy; then bad=0; else bad=$((bad + 1)); fi
    if ((bad >= BAD_IN_A_ROW)); then ok=0; break; fi
  done
else
  ok=0
fi

if ((ok)); then
  printf '%s\n' "$id" >"$ROOT/current"
  record deployed
  say "release $id is live on $env_name"
  exit 0
fi

if [[ -z "$previous" || ! -f "$ROOT/$previous/release.env" ]]; then
  record failed
  alert "release $id failed its health checks; no previous release to roll back to"
  die "release $id failed its health checks and there is no previous release"
fi
say "release $id failed its health checks: rolling back to $previous"
dir="$ROOT/$previous"
if compose "$dir" up -d --wait --wait-timeout 300; then
  record rolled_back
  alert "release $id failed its health checks; rolled back to $previous"
else
  record rollback_failed
  alert "release $id failed and the rollback to $previous is unhealthy too: act now"
fi
exit 2
