#!/usr/bin/env bash
# Runner side of a deploy (E16.4, .github/workflows/deploy.yml, docs/runbooks/deploy.md): join
# Netbird, copy this commit's compose/ + scripts/deploy.sh and the release file to the VM over SSH,
# run deploy.sh there, then probe SMOKE_URLS from the runner. Writes the job summary.
#
#   scripts/ci-deploy.sh <dev|staging|prod> <release id> <release.env>
#
# Env (GitHub environment secrets/vars, never in the repo):
#   NB_SETUP_KEY, NB_MANAGEMENT_URL  an ephemeral Netbird setup key of the `ci-deploy` group
#   DEPLOY_HOST, DEPLOY_USER         the VM's Netbird name/address and its deploy user
#   DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS  that user's private key and the VM's host key line
#   SBAM_PROFILES, SBAM_ALERT_WEBHOOK_URL, SBAM_DEPLOY_ACTOR  passed through to deploy.sh
#   SMOKE_URLS                       space-separated URLs that must answer 2xx/3xx afterwards
# shellcheck disable=SC2029 # remote commands are built here on purpose, every value quoted with %q
set -euo pipefail

NETBIRD_IMAGE="netbirdio/netbird:0.80.0@sha256:4976692ea44bb93871743d0b742b4a8f97f6425bfc458ca2d78e28fdb8bdf4ad"
env_name=$1 id=$2 release=$3
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
: "${NB_SETUP_KEY:?} ${DEPLOY_HOST:?} ${DEPLOY_USER:?} ${DEPLOY_SSH_KEY:?} ${DEPLOY_KNOWN_HOSTS:?}"

{
  echo "### Deploy \`$id\` to $env_name"
  echo
  echo '| Image | Digest |'
  echo '|---|---|'
  # shellcheck disable=SC2016 # markdown backticks
  grep -E '^SBAM_[A-Z_]+_IMAGE=' "$release" | sed -E 's/^([A-Z_]+)=(.*)$/| `\1` | `\2` |/'
  echo
} >>"$summary"

# Netbird: a throwaway peer for this job only (ephemeral key), host network so ssh uses wt0.
cleanup() { docker rm -f sbam-netbird >/dev/null 2>&1 || true; rm -rf "$HOME/.ssh/sbam-deploy"; }
trap cleanup EXIT
docker run -d --name sbam-netbird --network host --device /dev/net/tun \
  --cap-add NET_ADMIN --cap-add SYS_ADMIN --cap-add SYS_RESOURCE \
  -e NB_SETUP_KEY -e NB_MANAGEMENT_URL="${NB_MANAGEMENT_URL:-https://api.netbird.io}" \
  -e NB_HOSTNAME="gha-${GITHUB_RUN_ID:-local}-$env_name" "$NETBIRD_IMAGE" >/dev/null
for _ in $(seq 60); do
  docker exec sbam-netbird netbird status 2>/dev/null | grep -q '^Management: Connected' && break
  sleep 2
done
docker exec sbam-netbird netbird status | grep -q '^Management: Connected' || {
  echo "deploy: could not join Netbird" >&2
  exit 1
}

key_dir="$HOME/.ssh/sbam-deploy"
mkdir -p "$key_dir" && chmod 700 "$key_dir"
printf '%s\n' "$DEPLOY_SSH_KEY" >"$key_dir/id" && chmod 600 "$key_dir/id"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" >"$key_dir/known_hosts"
ssh_opts=(-i "$key_dir/id" -o UserKnownHostsFile="$key_dir/known_hosts" -o StrictHostKeyChecking=yes
  -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)
remote="$DEPLOY_USER@$DEPLOY_HOST"
incoming="sbam-incoming/$id" # under the deploy user's home

ssh "${ssh_opts[@]}" "$remote" "rm -rf $(printf %q "$incoming") && mkdir -p $(printf %q "$incoming")"
tar -czf - compose scripts/deploy.sh | ssh "${ssh_opts[@]}" "$remote" "tar -xzf - -C $(printf %q "$incoming")"
scp -q "${ssh_opts[@]}" "$release" "$remote:$incoming/release.env"

pass=""
for name in SBAM_PROFILES SBAM_ALERT_WEBHOOK_URL SBAM_DEPLOY_ACTOR; do
  pass+="$name=$(printf %q "${!name:-}") "
done
set +e
ssh "${ssh_opts[@]}" "$remote" "cd $(printf %q "$incoming") && ${pass}bash scripts/deploy.sh \
  --env $(printf %q "$env_name") --id $(printf %q "$id") --release release.env --bundle ."
rc=$?
set -e
case $rc in
  0) result="deployed (or already running)" ;;
  1) result="refused or aborted before rollout (old version still serving)" ;;
  2) result="failed its health checks: rolled back to the previous release" ;;
  *) result="ssh/deploy error ($rc)" ;;
esac
echo "**Result:** $result" >>"$summary"
((rc == 0)) || exit "$rc"

for url in ${SMOKE_URLS:-}; do
  # dev/staging serve SBAM_TLS=internal certificates: -k, the request goes over Netbird
  code="$(curl -ksS -o /dev/null -w '%{http_code}' -m 20 "$url" || echo 000)"
  echo "- smoke \`$url\`: $code" >>"$summary"
  [[ "$code" =~ ^[23] ]] || {
    echo "deploy: smoke check failed for $url ($code)" >&2
    exit 1
  }
done
