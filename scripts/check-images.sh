#!/usr/bin/env bash
# Build every SBAM image and run the E1.9 image checks (T01–T08). Needs only Docker.
# Usage: bash scripts/check-images.sh [--no-build]
set -uo pipefail
cd "$(dirname "$0")/.."

TAG=check
HADOLINT=hadolint/hadolint:v2.15.1@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d
TRIVY=aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
IMAGES=(sbam-api sbam-worker sbam-web sbam-toolchain sbam-promptfoo skill-runtime sbam-mock-api sbam-proxy)
fail=0
pass() { printf 'PASS  %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }

build() { # name dockerfile [extra docker build args...]
  local name=$1 file=$2; shift 2
  docker build -q -f "compose/docker/$file" "$@" -t "$name:$TAG" . >/dev/null || { bad "build $name"; return 1; }
}

if [[ "${1:-}" != "--no-build" ]]; then
  build sbam-api server.Dockerfile --target prod --build-arg PACKAGE=sbam-api --build-arg ROLE=api
  build sbam-worker server.Dockerfile --target prod --build-arg PACKAGE=sbam-workers --build-arg ROLE=worker
  build sbam-web web.Dockerfile --target prod
  build sbam-toolchain toolchain.Dockerfile
  build sbam-promptfoo promptfoo.Dockerfile
  build skill-runtime skill-runtime.Dockerfile
  build sbam-mock-api mock-api.Dockerfile
  build sbam-proxy proxy.Dockerfile
  pass "build all ${#IMAGES[@]} images"
fi

# T01 hadolint on every Dockerfile
for f in compose/docker/*.Dockerfile; do
  if docker run --rm -i "$HADOLINT" hadolint --ignore DL3008 - <"$f" >/tmp/hadolint.out 2>&1; then
    pass "T01 hadolint $(basename "$f")"
  else
    bad "T01 hadolint $(basename "$f")"; cat /tmp/hadolint.out
  fi
done

# T08 every FROM pinned by digest (stage aliases like `FROM base AS dev` are fine)
unpinned=$(grep -hE '^FROM ' compose/docker/*.Dockerfile | grep -vE '@sha256:[0-9a-f]{64}' | grep -vE '^FROM (base|deps|build|uv)( |$)')
[[ -z "$unpinned" ]] && pass "T08 every base image pinned by @sha256" || { bad "T08 unpinned FROM"; echo "$unpinned"; }

# T03 non-root
for img in "${IMAGES[@]}"; do
  uid=$(docker run --rm --entrypoint id "$img:$TAG" -u 2>/dev/null)
  [[ -n "$uid" && "$uid" != 0 ]] && pass "T03 $img runs as uid $uid" || bad "T03 $img runs as root"
done

# T04 api healthcheck: container becomes healthy (liveness needs no dependencies)
cid=$(docker run -d -e DATABASE_URL=postgresql://x@127.0.0.1:1/x -e STORAGE_ROOT=/data/storage \
  -e TEMPORAL_HOST=127.0.0.1:1 -e KEYCLOAK_ISSUER=http://127.0.0.1:1/realms/bullm -e KEYCLOAK_AUDIENCE=sbam-api \
  "sbam-api:$TAG")
state=starting
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$cid"); [[ $state == healthy ]] && break; sleep 1
done
[[ $state == healthy ]] && pass "T04 sbam-api healthy" || { bad "T04 sbam-api health=$state"; docker logs "$cid" | tail -5; }
docker rm -f "$cid" >/dev/null

# Worker stub and mock-api become healthy too
for img in sbam-worker sbam-mock-api; do
  cid=$(docker run -d "$img:$TAG"); state=starting
  for _ in $(seq 1 45); do state=$(docker inspect -f '{{.State.Health.Status}}' "$cid"); [[ $state == healthy ]] && break; sleep 1; done
  [[ $state == healthy ]] && pass "$img healthy" || bad "$img health=$state"
  docker rm -f "$cid" >/dev/null
done

# T05 runtime registry (#67 format): every top-level module of every package imports, offline
if docker run --rm --network none --entrypoint python "skill-runtime:$TAG" -c "
import importlib, json
reg = json.load(open('/opt/runtime_registry.json'))['packages']
for p in reg:
    for m in p['top_level_modules']:
        importlib.import_module(m)
print(len(reg), 'packages')
"; then pass "T05 runtime registry imports"; else bad "T05 runtime registry"; fi

# T06 ephemeral PostgreSQL 16 inside skill-runtime, on 127.0.0.1 only (sbam-pg-start, #67)
if docker run --rm --network none --entrypoint sh "skill-runtime:$TAG" -c '
  sbam-pg-start >/dev/null && psql -h 127.0.0.1 -U postgres -Atc "show listen_addresses" &&
  postgres --version' | tee /tmp/t06.out | grep -q "PostgreSQL) 16"; then
  pass "T06 $(tail -1 /tmp/t06.out)"
else
  bad "T06 PostgreSQL 16 in skill-runtime"; cat /tmp/t06.out
fi

# T02 Trivy: no CRITICAL vulnerabilities; T07 no secrets in layers
for img in "${IMAGES[@]}"; do
  if docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache "$TRIVY" \
      image --quiet --scanners vuln,secret --severity CRITICAL --exit-code 1 --ignore-unfixed "$img:$TAG" >/tmp/trivy.out 2>&1; then
    pass "T02/T07 $img: 0 CRITICAL, 0 secrets"
  else
    bad "T02/T07 $img"; tail -20 /tmp/trivy.out
  fi
done

echo
[[ $fail == 0 ]] && echo "ALL IMAGE CHECKS PASSED" || echo "SOME IMAGE CHECKS FAILED"
exit $fail
