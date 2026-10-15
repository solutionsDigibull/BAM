#!/usr/bin/env bash
# TLS certificate expiry check (E16.3, #142): exit 1 when the certificate served for a host expires within N days
# (default 14), so a failed ACME renewal is noticed two weeks before users are. Run it daily from cron or any
# monitor that can run a command, and alert on a non-zero exit:
#   0 6 * * *  /opt/sbam/scripts/check-cert-expiry.sh portal.example.com 14 || mail -s "SBAM cert expiry" ops@example.com
# Usage: check-cert-expiry.sh <host> [days=14] [port=443]. Needs openssl and network access to the host.
set -u
host=${1:?usage: $0 <host> [days=14] [port=443]}
days=${2:-14}
port=${3:-443}

end=$(echo | openssl s_client -servername "$host" -connect "$host:$port" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null)
if [ -z "$end" ]; then echo "FAIL $host:$port: could not read a certificate"; exit 2; fi
if echo | openssl s_client -servername "$host" -connect "$host:$port" 2>/dev/null | openssl x509 -noout -checkend $((days * 86400)) >/dev/null; then
  echo "OK    $host: ${end#notAfter=} (more than $days days left)"
else
  echo "FAIL $host: ${end#notAfter=} (expires within $days days)"
  exit 1
fi
