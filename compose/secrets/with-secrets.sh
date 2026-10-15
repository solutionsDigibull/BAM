#!/bin/sh
# Secret loader for the staging/prod/dev deploy overlay (E16.3, #142). Run as the container entrypoint:
#   /bin/sh /sbam/with-secrets.sh <original entrypoint and command...>
#
# For every NAME_FILE=<path> in the environment it exports NAME=<file contents without trailing newline>
# and drops NAME_FILE, so the secret exists only in this process tree, never in the Compose file, the
# image or `docker inspect`. Docker mounts the files at /run/secrets/<name> (mode 0400, owned by the
# container's user). DATABASE_URL_TEMPLATE may contain __SBAM_DB_PASSWORD__, which becomes DATABASE_URL.
set -eu

for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)_FILE=.*/\1/p'); do
  eval "path=\${${name}_FILE}"
  if [ ! -r "$path" ]; then
    echo "with-secrets: $path (for $name) is missing or not readable by uid $(id -u)" >&2
    exit 78
  fi
  value=$(cat "$path")
  export "$name=$value"
  unset "${name}_FILE"
done

if [ -n "${DATABASE_URL_TEMPLATE:-}" ]; then
  : "${SBAM_DB_PASSWORD:?DATABASE_URL_TEMPLATE needs SBAM_DB_PASSWORD_FILE}"
  head=${DATABASE_URL_TEMPLATE%%__SBAM_DB_PASSWORD__*}
  tail=${DATABASE_URL_TEMPLATE#*__SBAM_DB_PASSWORD__}
  export DATABASE_URL="$head$SBAM_DB_PASSWORD$tail"
  unset DATABASE_URL_TEMPLATE
fi

exec "$@"
