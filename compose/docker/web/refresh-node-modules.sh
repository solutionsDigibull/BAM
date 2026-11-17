#!/bin/sh
# Keeps the dev web container's node_modules volume in step with the image (#817).
# Docker fills a named volume only when it is created, so a later lockfile change never reached it. This runs
# in the one-shot `web-deps` service (compose.override.yaml) from the freshly built image: if the image's
# pnpm-lock.yaml differs from the hash recorded in the volume, the volume gets the image's node_modules.
# Paths come from the environment so the script can be tested; the defaults are the ones in the container.
set -eu
LOCK="${LOCK:-/app/client/pnpm-lock.yaml}"
SRC="${SRC:-/app/client/node_modules}"
DST="${DST:-/nm}"
MARK="$DST/.sbam-lock-sha"

now="$(sha256sum "$LOCK" | cut -d' ' -f1)"
if [ -f "$MARK" ] && [ "$(cat "$MARK")" = "$now" ]; then
  echo "node_modules is current ($now)"
  exit 0
fi

echo "pnpm-lock.yaml changed: refreshing node_modules from the image"
find "$DST" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "$SRC"/. "$DST"/
echo "$now" > "$MARK"
echo "node_modules refreshed ($now)"
