#!/usr/bin/env bash
# Verify the secret files of a deployed environment (E16.3, #142). Run it on the VM before `docker compose up`.
#
#   scripts/check-secrets.sh [--env dev|staging|prod] [--dir /etc/sbam/secrets] [--env-file /etc/sbam/<env>.env]
#
# Checks: every secret that compose.deploy.yaml mounts exists as a regular file (no symlink), is mode 0400,
# owned by root, non-empty and not a template placeholder; database passwords are URL-safe (they are placed in a
# connection URL); and the VM's env file holds no real secret, only empty or placeholder values (the real values are
# only in the secret files). Prints no secret. Exit 1 when anything fails. Provision with:
#   install -m 0400 -o root -g root /dev/stdin /etc/sbam/secrets/<name> <<<"$(openssl rand -hex 32)"
set -u

DIR=${SBAM_SECRETS_DIR:-/etc/sbam/secrets}
ENV_NAME=prod
ENV_FILE=
OWNER_UID=${SBAM_SECRETS_OWNER_UID:-0} # tests run unprivileged and set this to their own uid

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_NAME=$2; shift 2 ;;
    --dir) DIR=$2; shift 2 ;;
    --env-file) ENV_FILE=$2; shift 2 ;;
    *) echo "usage: $0 [--env dev|staging|prod] [--dir DIR] [--env-file FILE]" >&2; exit 2 ;;
  esac
done

# Keep in sync with the top-level `secrets:` of compose/compose.deploy.yaml (a unit test compares them).
DB_SECRETS="postgres_password sbam_db_password temporal_db_password keycloak_db_password langfuse_db_password"
OTHER_SECRETS="keycloak_admin_password keycloak_admin_client_secret tusd_hook_secret upload_token_key storage_signing_key auth_secret openrouter_api_key cognee_user_secret bifrost_virtual_key"

fail=0
bad() { echo "FAIL $1: $2"; fail=1; }

[ -d "$DIR" ] || { echo "FAIL $DIR: secrets directory does not exist"; exit 1; }

for name in $DB_SECRETS $OTHER_SECRETS; do
  f="$DIR/$name"
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then bad "$name" "missing"; continue; fi
  if [ -L "$f" ] || [ ! -f "$f" ]; then bad "$name" "must be a regular file, not a symlink or directory"; continue; fi
  mode=$(stat -c '%a' "$f"); owner=$(stat -c '%u' "$f")
  [ "$mode" = "400" ] || bad "$name" "mode is $mode, must be 0400"
  [ "$owner" = "$OWNER_UID" ] || bad "$name" "owned by uid $owner, must be uid $OWNER_UID"
  value=$(cat "$f" 2>/dev/null || true)
  if [ -z "$value" ]; then
    # Dev VMs use the mock LLM provider and need no OpenRouter key; the file must still exist.
    if [ "$name" = openrouter_api_key ] && [ "$ENV_NAME" = dev ]; then continue; fi
    bad "$name" "empty"; continue
  fi
  case "$value" in *change-me*|*changeme*|*CHANGE_ME*) bad "$name" "still a template placeholder"; continue ;; esac
  if [ "$(printf '%s' "$value" | wc -l)" -ne 0 ]; then bad "$name" "contains a newline (one line, no trailing text)"; continue; fi
  case " $DB_SECRETS " in
    *" $name "*)
      printf '%s' "$value" | grep -Eq '^[A-Za-z0-9._~-]{16,}$' ||
        bad "$name" "must be 16+ URL-safe characters [A-Za-z0-9._~-] (use openssl rand -hex 32)" ;;
    *)
      min=16
      [ "$name" = cognee_user_secret ] && min=32 # the knowledge worker preflight refuses a shorter one (A-09)
      if [ "$name" != openrouter_api_key ] && [ "${#value}" -lt "$min" ]; then bad "$name" "shorter than $min characters"; fi ;;
  esac
done

# The upload-token key must not be the tusd hook secret: rotating or leaking one must not touch the other.
if [ -f "$DIR/tusd_hook_secret" ] && [ -f "$DIR/upload_token_key" ] && cmp -s "$DIR/tusd_hook_secret" "$DIR/upload_token_key"; then
  bad upload_token_key "must differ from tusd_hook_secret"
fi

if [ -n "$ENV_FILE" ]; then
  [ -r "$ENV_FILE" ] || bad "$ENV_FILE" "env file not readable"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key=${line%%=*}; val=${line#*=}
    if printf '%s' "$key" | grep -Eqi '(PASSWORD|SECRET|TOKEN|API_KEY|SALT|ENCRYPTION_KEY|SIGNING_KEY)$'; then
      case "$val" in ''|change-me*|unused*) ;; *) bad "$ENV_FILE" "$key holds a real-looking value; secrets belong in $DIR only" ;; esac
    fi
  done < "$ENV_FILE"
fi

# Stray files are a smell (old rotations, editor backups) and are readable by root only anyway.
for f in "$DIR"/*; do
  [ -e "$f" ] || continue
  case " $DB_SECRETS $OTHER_SECRETS " in *" $(basename "$f") "*) ;; *) echo "WARN  $(basename "$f"): not a secret of this release" ;; esac
done

[ "$fail" = 0 ] && echo "OK    secrets in $DIR ($ENV_NAME)"
exit "$fail"
