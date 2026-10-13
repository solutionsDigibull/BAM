#!/bin/bash
# Local/dev seed for realm `bullm` (#47, E2.1): two tenants and their users. Runs as the one-off
# `keycloak-seed` job from compose.override.yaml, never in staging/prod (the realm file has no users).
# Re-runnable: everything is created only if missing; passwords are reset to SEED_USER_PASSWORD.
#
# Tenants are Keycloak Organizations whose alias IS the tenant UUID, so the token carries
# "tenant_id": ["<uuid>"] (ADR 0002, amended). The UUIDs are fixed so tests and fixtures can use them.
set -euo pipefail

kc=/opt/keycloak/bin/kcadm.sh
realm=bullm
TENANT_A=6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11
TENANT_B=8d2b9c4e-5f60-4a18-b3c7-2e9d4f1a6c22

"$kc" config credentials --server "${KC_URL:-http://keycloak:8080}" --realm master \
  --user admin --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null

csv() { "$kc" get "$@" -r "$realm" --format csv --noquotes; }

org_id() { # alias. `search` matches name/domain, not alias, so filter here (the image has no awk).
  csv organizations -q max=1000 --fields id,alias | grep ",$1\$" | cut -d, -f1 || true
}

ensure_org() { # alias name domain
  if [ -z "$(org_id "$1")" ]; then
    "$kc" create organizations -r "$realm" -s alias="$1" -s name="$2" \
      -s "domains=[{\"name\": \"$3\"}]" >/dev/null
    echo "created org $2 ($1)"
  fi
}

user_id() { # username
  csv users -q username="$1" -q exact=true --fields id
}

ensure_user() { # username role [org alias...]
  local username=$1 role=$2
  shift 2
  if [ -z "$(user_id "$username")" ]; then
    "$kc" create users -r "$realm" -s username="$username" -s email="$username@sbam.test" \
      -s emailVerified=true -s enabled=true -s firstName="$username" -s lastName=Seed >/dev/null
    echo "created user $username"
  fi
  "$kc" set-password -r "$realm" --username "$username" --new-password "$SEED_USER_PASSWORD"
  "$kc" add-roles -r "$realm" --uusername "$username" --rolename "$role"
  local uid
  uid=$(user_id "$username")
  for alias in "$@"; do
    local oid
    oid=$(org_id "$alias")
    if ! csv "organizations/$oid/members" --fields username | grep -qx "$username"; then
      "$kc" create "organizations/$oid/members" -r "$realm" -b "\"$uid\"" >/dev/null
      echo "added $username to $alias"
    fi
  done
}

ensure_api_admin_client() { # sbam-api's service account for tenant organizations (#49)
  if [ -z "${KEYCLOAK_ADMIN_CLIENT_SECRET:-}" ]; then
    echo "KEYCLOAK_ADMIN_CLIENT_SECRET unset: skipping sbam-api-admin (tenant creation answers 503)"
    return
  fi
  local cid
  cid=$(csv clients -q clientId=sbam-api-admin --fields id)
  if [ -z "$cid" ]; then
    cid=$("$kc" create clients -r "$realm" -s clientId=sbam-api-admin -s publicClient=false \
      -s serviceAccountsEnabled=true -s standardFlowEnabled=false -s directAccessGrantsEnabled=false -i)
    echo "created client sbam-api-admin"
  fi
  "$kc" update "clients/$cid" -r "$realm" -s "secret=$KEYCLOAK_ADMIN_CLIENT_SECRET"
  # The organizations admin API needs manage-realm (manage-users is refused, Keycloak 26.8).
  "$kc" add-roles -r "$realm" --uusername service-account-sbam-api-admin \
    --cclientid realm-management --rolename manage-realm
}

ensure_api_admin_client
ensure_org "$TENANT_A" "Tenant A" tenant-a.test
ensure_org "$TENANT_B" "Tenant B" tenant-b.test

ensure_user super-admin super-admin # no organization: no tenant_id claim
ensure_user admin-a sbam-admin "$TENANT_A"
ensure_user fde-a1 sbam-fde "$TENANT_A"
ensure_user fde-a2 sbam-fde "$TENANT_A"
ensure_user admin-b sbam-admin "$TENANT_B"
ensure_user fde-b1 sbam-fde "$TENANT_B"
ensure_user fde-ab sbam-fde "$TENANT_A" "$TENANT_B" # two orgs: claim absent unless one is selected
echo "seed complete"
