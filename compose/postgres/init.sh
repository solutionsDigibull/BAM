#!/bin/sh
# Creates one login role + database per consumer (TD §20.3.4). The postgres entrypoint runs this only
# when the pgdata volume is empty, so it never runs twice; `make reset CONFIRM=yes` starts over.
set -eu

create() { # <role> <database> <password>
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v role="$1" -v db="$2" -v pw="$3" <<'SQL'
CREATE ROLE :"role" LOGIN PASSWORD :'pw';
CREATE DATABASE :"db" OWNER :"role";
SQL
}

create sbam sbam "$SBAM_DB_PASSWORD"
create temporal temporal "$TEMPORAL_DB_PASSWORD"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -c 'CREATE DATABASE temporal_visibility OWNER temporal;'
create keycloak keycloak "$KEYCLOAK_DB_PASSWORD"
create langfuse langfuse "$LANGFUSE_DB_PASSWORD"
