#!/usr/bin/env bash
# Builds the CubeSandbox template for one skill-runtime digest and registers it under the alias
# `sbam-rt-<first 56 hex of the digest>` (WG-1, ADR 0023 decisions 4, 5 and 7, docs/runbooks/sandbox-host.md).
#
# Run it ON the CubeSandbox host (it needs `docker` with the runtime image, and `cubemastercli`), from a checkout:
#   scripts/sandbox-template.sh                       # the local skill-runtime:local image
#   scripts/sandbox-template.sh --runtime-image skill-runtime@sha256:<digest>
#   scripts/sandbox-template.sh --force               # rebuild and re-register
#
# Idempotent: when a READY template already carries the alias it does nothing (unless --force).
# It reads no secrets and prints none.
set -euo pipefail

RUNTIME_IMAGE="${SKILL_RUNTIME_IMAGE:-skill-runtime:local}"
REGISTRY="${TEMPLATE_REGISTRY:-127.0.0.1:5000}"   # plain-HTTP registry the host's CubeMaster can pull from
CUBE_BASE_IMAGE="${CUBE_BASE_IMAGE:-}"            # empty keeps the digest pinned in the Dockerfile
CPU_MILLI="${TEMPLATE_CPU_MILLI:-1000}"           # the template, not the call, sets a VM's size (ADR 0023)
MEMORY_MB="${TEMPLATE_MEMORY_MB:-1024}"
WRITABLE_LAYER="${TEMPLATE_WRITABLE_LAYER:-1G}"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --runtime-image) RUNTIME_IMAGE="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "sandbox-template: $1 is required on this host" >&2; exit 1; }; }
need docker; need cubemastercli

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The digest the manifests record: the registry digest of a pinned image, else the image id (a laptop build).
if [[ "$RUNTIME_IMAGE" == *@sha256:* ]]; then
  DIGEST="sha256:${RUNTIME_IMAGE##*@sha256:}"
else
  DIGEST="$(docker image inspect --format '{{.Id}}' "$RUNTIME_IMAGE")" \
    || { echo "sandbox-template: $RUNTIME_IMAGE is not on this host (docker load it, or build it with 'make runtime-registry')" >&2; exit 1; }
fi
HEX="${DIGEST#sha256:}"
[[ "$HEX" =~ ^[0-9a-f]{64}$ ]] || { echo "sandbox-template: not a sha256 digest: $DIGEST" >&2; exit 1; }
ALIAS="sbam-rt-${HEX:0:56}"   # 8 + 56 = 64, the host's alias limit; the in-VM check compares all 64 hex
IMAGE="$REGISTRY/sbam/skill-runtime-vm:$HEX"

if [ "$FORCE" -eq 0 ] && cubemastercli tpl list 2>/dev/null | awk -v a="$ALIAS" '$2==a && $3=="READY" {f=1} END {exit !f}'; then
  echo "sandbox-template: $ALIAS is already READY (use --force to rebuild)"
  exit 0
fi

echo "sandbox-template: runtime digest $DIGEST"
echo "sandbox-template: alias         $ALIAS"

# A small build context: only what the Dockerfile copies, not the whole checkout.
BUILD_ARGS=(--build-arg "RUNTIME_IMAGE=$RUNTIME_IMAGE" --build-arg "RUNTIME_DIGEST=$DIGEST")
[ -n "$CUBE_BASE_IMAGE" ] && BUILD_ARGS+=(--build-arg "CUBE_BASE_IMAGE=$CUBE_BASE_IMAGE")
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - \
    compose/docker/sandbox-template.Dockerfile \
    compose/sandbox-runner/harness \
    server/contracts/sbam_contracts \
    server/workers/sbam_workers \
  | docker build -q -f compose/docker/sandbox-template.Dockerfile "${BUILD_ARGS[@]}" -t "$IMAGE" - >/dev/null
docker push -q "$IMAGE" >/dev/null

# `http://` makes CubeMaster pull from a plain-HTTP registry (the host's own, on loopback).
cubemastercli tpl create-from-image \
  --image "http://$IMAGE" \
  --alias "$ALIAS" \
  --writable-layer-size "$WRITABLE_LAYER" \
  --expose-port 49983 --probe 49983 --probe-path /health \
  --cpu "$CPU_MILLI" --memory "$MEMORY_MB"

echo "sandbox-template: $ALIAS is registered"
