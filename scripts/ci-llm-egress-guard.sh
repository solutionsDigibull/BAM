#!/usr/bin/env bash
# E1.15-T07: CI must make zero connections to openrouter.ai (LLM_PROVIDER=mock).
#   start   resolve openrouter.ai and put counting REJECT rules in DOCKER-USER (blocks and counts container egress)
#   verify  fail if any rule matched a packet, or if bifrost holds an OpenRouter key under the mock
#   stop    remove the rules
# Runs on a GitHub runner (passwordless sudo, Docker iptables). Needs ENV_FILE for `verify` (compose env).
set -euo pipefail

HOST=openrouter.ai
CHAIN=DOCKER-USER
COMMENT=sbam-ci-no-openrouter

ips() { getent ahostsv4 "$HOST" | awk '{print $1}' | sort -u; }

rules() { sudo iptables -S "$CHAIN" | grep -- "--comment $COMMENT" || true; }

case "${1:-}" in
  start)
    found=0
    for ip in $(ips); do
      sudo iptables -I "$CHAIN" -d "$ip" -m comment --comment "$COMMENT" -j REJECT
      echo "blocking and counting egress to $HOST ($ip)"
      found=1
    done
    [ "$found" = 1 ] || { echo "::error::could not resolve $HOST, the egress check would be blind"; exit 1; }
    ;;
  verify)
    hits="$(sudo iptables -L "$CHAIN" -n -v -x | awk -v c="$COMMENT" '$0 ~ c && $1 > 0 {print}')"
    if [ -n "$hits" ]; then
      echo "::error::CI attempted connections to $HOST:"; echo "$hits"; exit 1
    fi
    [ -n "$(rules)" ] || { echo "::error::egress rules are gone, the check proved nothing"; exit 1; }
    # Belt and braces for CDN address drift: no key under the mock, so a real call could not authenticate.
    compose=(docker compose --env-file "${ENV_FILE:?set ENV_FILE}" -f compose/compose.yaml -f compose/compose.override.yaml)
    key="$("${compose[@]}" exec -T bifrost printenv OPENROUTER_API_KEY || true)"
    [ -z "$key" ] || { echo "::error::bifrost has an OpenRouter key while LLM_PROVIDER=mock"; exit 1; }
    echo "0 connections to $HOST"
    ;;
  stop)
    while IFS= read -r rule; do
      # shellcheck disable=SC2086
      sudo iptables ${rule/-A/-D}
    done < <(rules)
    ;;
  *) echo "usage: $0 start|verify|stop" >&2; exit 2 ;;
esac
