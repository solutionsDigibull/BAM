#!/bin/sh
# Ephemeral PostgreSQL 16 for one sandbox/test run (TD §15.2, E12.1-T05).
# Data lives in $PGDATA (under /tmp, thrown away with the container). It listens on 127.0.0.1 only
# (never a container-external address) with trust auth, which is safe only because of that.
# Usage: sbam-pg-start            -> server running; then `psql -h 127.0.0.1 -U postgres`
set -eu
PGDATA="${PGDATA:-/tmp/pgdata}"
export PGDATA
initdb -D "$PGDATA" -U postgres --auth=trust --no-instructions >/dev/null
pg_ctl -D "$PGDATA" -l /tmp/postgresql.log -w -t 10 \
  -o "-c listen_addresses=127.0.0.1 -k /tmp" start >/dev/null
pg_isready -h 127.0.0.1 -t 10 >/dev/null
echo "postgresql ready on 127.0.0.1:5432"
