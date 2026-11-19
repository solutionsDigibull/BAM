#!/usr/bin/env bash
# The Staging end-to-end proof on the LOCAL stack with the MOCK LLM (WG-31, #908, docs/runbooks/staging-runtime.md).
#
#   make up PROFILES=staging-runtime          # the stack with the Staging runtime on (LLM_PROVIDER=mock)
#   scripts/staging-proof.sh | tee proof-output.txt
#
# What it does: brings the database to Alembic head, builds two tiny test-only images on THIS machine's Docker daemon
# (the one the socket proxy reaches), runs server/tests/staging_proof/driver.py inside worker-certify (seeds a committed
# workflow, starts it through the real Staging runtime, calls the real API as the FDE, another FDE and another tenant),
# then checks from the host: the egress proxy logged the blocked host, the superseded container stopped, the containers
# sit on the internal network, no worker holds docker.sock, and the socket proxy refuses exec, a privileged create and
# a bind mount. Prints no secret. Exit 0 only when every check passed.
#
# Settings (environment, or compose/.env values, never printed): ENV_FILE (default compose/.env), COMPOSE_PROJECT_NAME,
# KEYCLOAK_PORT, WEB_PORT, SEED_USER_PASSWORD, PROOF_KEEP=1 (leave the Staging containers running afterwards).
# Not production code: it seeds a WORKFLOW certificate (#130 is not merged) and a stand-in Bifrost, see the driver.
set -uo pipefail

cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: keep /app/... paths as they are
ENV_FILE=${ENV_FILE:-compose/.env}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE: copy compose/.env.example, or set ENV_FILE" >&2; exit 2; }

envv() { # NAME DEFAULT: the process environment first, then ENV_FILE; never echoed by this script
  local v=${!1:-}
  [ -n "$v" ] || v=$(grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2-)
  printf '%s' "${v:-$2}"
}

PROJECT_FLAG=()
[ -n "${COMPOSE_PROJECT_NAME:-}" ] && PROJECT_FLAG=(-p "$COMPOSE_PROJECT_NAME")
COMPOSE=(docker compose "${PROJECT_FLAG[@]}" --env-file "$ENV_FILE" -f compose/compose.yaml -f compose/compose.override.yaml -f compose/compose.staging-runtime.yaml)
KC_PORT=$(envv KEYCLOAK_PORT 8080)
WEB_PORT=$(envv WEB_PORT 3000)
SEED_USER_PASSWORD=$(envv SEED_USER_PASSWORD "")
export SEED_USER_PASSWORD
LLM=$(envv LLM_PROVIDER mock)

fails=0
host_check() { # id ok detail
  if [ "$2" = 1 ]; then echo "CHECK $1 PASS $3"; else echo "CHECK $1 FAIL $3"; fails=$((fails + 1)); fi
}

echo "== staging proof, $(date -u +%FT%TZ), LLM_PROVIDER=$LLM"
[ "$LLM" = mock ] || { echo "refusing to run: LLM_PROVIDER is '$LLM', the proof uses the mock only" >&2; exit 2; }
[ -n "$SEED_USER_PASSWORD" ] || { echo "SEED_USER_PASSWORD is not set (the seed users' password)" >&2; exit 2; }

for service in docker-socket-proxy staging-egress-proxy worker-certify sbam-api; do
  "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx "$service" ||
    { echo "$service is not running: make up PROFILES=staging-runtime" >&2; exit 2; }
done

echo "== 1. migrations: alembic upgrade head, then confirm the database is at head"
"${COMPOSE[@]}" exec -T sbam-api python -m sbam_api.db.migrate upgrade head >/dev/null 2>&1 || true
current=$("${COMPOSE[@]}" exec -T sbam-api python -m sbam_api.db.migrate current 2>&1 | grep -Eo '^[0-9a-z_]+' | head -1)
heads=$("${COMPOSE[@]}" exec -T sbam-api python -m sbam_api.db.migrate heads 2>&1 | grep -Eo '^[0-9a-z_]+' | head -1)
host_check "WG31-T01a" $([ -n "$heads" ] && [ "$current" = "$heads" ] && echo 1 || echo 0) "database revision $current, head $heads"

echo "== 2. default stack unchanged: the worker's default runtime is none without the profile (static) and the stack is healthy"
unhealthy=$("${COMPOSE[@]}" ps --format '{{.Name}} {{.Health}}' 2>/dev/null | grep -Ev ' (healthy|)$' || true)
host_check "WG31-T01" $([ -z "$unhealthy" ] && echo 1 || echo 0) "services not healthy: ${unhealthy:-none}"

echo "== 3. two test-only images on this daemon (the one the socket proxy reaches)"
build() { docker build -q --build-arg "PROOF_VERSION=$1" -f server/tests/staging_proof/Dockerfile -t "sbam-staging-proof:$1" server/tests/staging_proof >/dev/null && docker image inspect --format '{{.Id}}' "sbam-staging-proof:$1"; }
IMAGE_V1=$(build 1.0.0)
IMAGE_V2=$(build 1.0.1)
echo "image 1.0.0 $IMAGE_V1"
echo "image 1.0.1 $IMAGE_V2"

echo "== 4. the live run inside worker-certify (real runtime, real API, real Keycloak logins)"
driver_out=$(mktemp)
"${COMPOSE[@]}" exec -T -e SEED_USER_PASSWORD worker-certify python /app/server/tests/staging_proof/driver.py \
  --issuer "http://localhost:$KC_PORT/realms/bullm" --web-port "$WEB_PORT" --image-v1 "$IMAGE_V1" --image-v2 "$IMAGE_V2" 2>&1 | tee "$driver_out"
driver_rc=${PIPESTATUS[0]}
prefix=$(grep -Eo '"container_prefix": "[^"]+"' "$driver_out" | cut -d'"' -f4)
fails=$((fails + driver_rc))

echo "== 5. host-side checks"
if [ -n "$prefix" ]; then
  blocked=$("${COMPOSE[@]}" logs --no-color staging-egress-proxy 2>&1 | grep -c "staging egress blocked: deployment=$prefix.* host=example.org")
  host_check "WG31-T03c" $([ "$blocked" -ge 1 ] && echo 1 || echo 0) "egress proxy logged the blocked host (sbam.staging.egress): $blocked line(s)"
  old=$(docker ps -a --filter "name=$prefix" --format '{{.Names}} {{.State}}' | grep -- '-v1-0-stg' || true)
  new=$(docker ps -a --filter "name=$prefix" --format '{{.Names}} {{.State}}' | grep -- '-v1-1-stg' || true)
  host_check "WG31-T04c" $(case "$old|$new" in *exited*"|"*running*) echo 1 ;; *) echo 0 ;; esac) "old: ${old##* }, new: ${new##* }"
  network=$(docker network inspect "$(envv STAGING_NETWORK sbam-staging)" --format '{{.Internal}}')
  host_check "WG31-T02h" $([ "$network" = true ] && echo 1 || echo 0) "Staging network internal=$network"
  hardened=$(docker inspect --format '{{.HostConfig.Privileged}} {{len .HostConfig.CapDrop}} {{len .HostConfig.Binds}}' "$(echo "$new" | cut -d' ' -f1)")
  host_check "WG31-T02i" $([ "$hardened" = "false 1 0" ] && echo 1 || echo 0) "new container privileged/cap_drop/binds: $hardened"
fi
mounts=$(for w in $("${COMPOSE[@]}" ps --services | grep '^worker-'); do "${COMPOSE[@]}" ps -q "$w" | xargs -r docker inspect --format '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}'; done | grep -c 'docker.sock')
host_check "WG31-T05" $([ "$mounts" = 0 ] && echo 1 || echo 0) "workers holding docker.sock: $mounts"
refused() { local out; out=$("${COMPOSE[@]}" exec -T worker-certify "$@" 2>&1) || true; grep -qE "403 Forbidden|Request forbidden" <<<"$out"; }
refused docker ps; ps_rc=$?
refused docker exec "$(docker ps -q --filter "name=$prefix" | head -1)" id; exec_rc=$?
refused docker run --rm --privileged --name "stg-evil-$$" --network "$(envv STAGING_NETWORK sbam-staging)" "$IMAGE_V1"; priv_rc=$?
refused docker run --rm -v /:/host --name "stg-evil2-$$" --network "$(envv STAGING_NETWORK sbam-staging)" "$IMAGE_V1"; bind_rc=$?
host_check "WG31-T05b" $([ $ps_rc = 0 ] && [ $exec_rc = 0 ] && [ $priv_rc = 0 ] && [ $bind_rc = 0 ] && echo 1 || echo 0) "socket proxy refused: list=$ps_rc exec=$exec_rc privileged=$priv_rc bind=$bind_rc (0 = refused)"

if [ "${PROOF_KEEP:-0}" != 1 ] && [ -n "$prefix" ]; then
  docker ps -aq --filter "name=$prefix" | xargs -r docker rm -f >/dev/null
  echo "removed the Staging containers of this run (PROOF_KEEP=1 keeps them)"
fi
rm -f "$driver_out"
if [ "$fails" = 0 ]; then echo "== STAGING PROOF PASSED"; else echo "== STAGING PROOF FAILED ($fails)"; fi
exit $((fails > 0))
