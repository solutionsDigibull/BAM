#!/usr/bin/env bash
# Live exposure checks for a deployed environment (E16.3-T01, T02, T06, T08). Run it from OUTSIDE the network
# (a laptop that is not on Netbird, or any internet host) against the production hostnames. Read-only: it
# connects, it changes nothing. Needs nmap and curl; testssl.sh is optional.
#
#   scripts/verify-public-exposure.sh <portal-host> <auth-host>
#
# T01  nmap: only 80 and 443 open; 80 redirects to https
# T02  Temporal UI / Grafana / Langfuse / PostHog on the public hostnames: 404 or unreachable
# T06  Keycloak admin console and admin REST through the public hostnames: 403/404
# T08  testssl.sh (if installed): TLS 1.2+ only, HSTS, no weak ciphers
set -u
portal=${1:?usage: $0 <portal-host> <auth-host>}
auth=${2:?usage: $0 <portal-host> <auth-host>}
fail=0
ok() { echo "OK    $*"; }
bad() { echo "FAIL  $*"; fail=1; }

# T01 ports
if command -v nmap >/dev/null; then
  open=$(nmap -Pn -p- --min-rate 2000 -oG - "$portal" 2>/dev/null | sed -n 's/.*Ports: //p' | tr ',' '\n' | awk -F/ '$2=="open"{gsub(/ /,"",$1); print $1}' | sort -n | tr '\n' ' ')
  [ "$open" = "80 443 " ] && ok "T01 only 80 and 443 are open on $portal" || bad "T01 open ports on $portal: ${open:-none} (expected 80 443)"
else
  bad "T01 nmap is not installed: cannot check open ports"
fi
loc=$(curl -sS -o /dev/null -m 10 -w '%{http_code} %{redirect_url}' "http://$portal/" 2>/dev/null)
case "$loc" in 30[1278]\ https://*) ok "T01 http://$portal/ redirects to https ($loc)" ;; *) bad "T01 http://$portal/ did not redirect to https ($loc)" ;; esac

# T02 ops UIs through the public hostnames (the proxy routes none of them)
for path in /temporal /grafana /langfuse /posthog /ui; do
  code=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "https://$portal$path" 2>/dev/null)
  case "$code" in 404|000) ok "T02 https://$portal$path -> $code" ;; *) bad "T02 https://$portal$path -> $code (expected 404)" ;; esac
done
for port in 8233 3002 3001 8010 8080 7233 5432; do
  if (exec 3<>"/dev/tcp/$portal/$port") 2>/dev/null; then bad "T02 $portal:$port accepts connections from outside"; else ok "T02 $portal:$port unreachable"; fi
done

# T06 Keycloak admin
for url in "https://$portal/admin/master/console" "https://$auth/admin/master/console/" "https://$auth/admin/realms/bullm/users" "https://$auth/realms/master/.well-known/openid-configuration"; do
  code=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null)
  case "$code" in 403|404) ok "T06 $url -> $code" ;; *) bad "T06 $url -> $code (expected 403/404)" ;; esac
done
code=$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "https://$auth/realms/bullm/.well-known/openid-configuration" 2>/dev/null)
[ "$code" = 200 ] && ok "T06 realm bullm login discovery is public (200)" || bad "T06 realm bullm discovery -> $code (login must work)"

# T08 TLS
hsts=$(curl -sSI -m 10 "https://$portal/" 2>/dev/null | tr -d '\r' | grep -i '^strict-transport-security:')
[ -n "$hsts" ] && ok "T08 $hsts" || bad "T08 no Strict-Transport-Security header on $portal"
for v in tls1 tls1_1; do
  if openssl s_client -connect "$portal:443" -servername "$portal" "-$v" </dev/null 2>&1 | grep -q 'BEGIN CERTIFICATE'; then bad "T08 $v accepted"; else ok "T08 $v refused"; fi
done
openssl s_client -connect "$portal:443" -servername "$portal" -tls1_2 </dev/null 2>&1 | grep -q 'BEGIN CERTIFICATE' && ok "T08 tls1_2 accepted" || bad "T08 tls1_2 refused"
if command -v testssl.sh >/dev/null; then
  testssl.sh --quiet --protocols --headers --cipher-per-proto "$portal:443" || bad "T08 testssl.sh reported problems (read its output)"
else
  echo "SKIP  T08 testssl.sh not installed: run it for the full cipher report"
fi
exit "$fail"
