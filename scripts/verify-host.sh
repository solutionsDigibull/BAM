#!/usr/bin/env bash
# Verifies a provisioned SBAM host (E0.14, docs/runbooks/prerequisites.md). Run it on the host itself,
# over Netbird, and paste the output into the checklist:
#
#   scripts/verify-host.sh dev-vm        # shared dev VM:   Docker, >= 32 GB RAM, no public ops ports
#   scripts/verify-host.sh sandbox-host  # CubeSandbox host: KVM, no public ops ports
#
# Env overrides: SECRET_FILE (default /etc/sbam/secrets/openrouter_api_key), SERVICE_USER (default sbam),
# NETBIRD_IF (default wt0), MIN_RAM_GB, MIN_CPUS. Exit status is non-zero if any check fails.
set -u

role="${1:-}"
case "$role" in
  dev-vm | sandbox-host) ;;
  *) echo "usage: $0 dev-vm|sandbox-host" >&2; exit 2 ;;
esac
# ponytail: same floor for both roles until the CubeSandbox spike (#28) sizes the sandbox host.
min_ram="${MIN_RAM_GB:-32}"
min_cpus="${MIN_CPUS:-8}"
secret_file="${SECRET_FILE:-/etc/sbam/secrets/openrouter_api_key}"
service_user="${SERVICE_USER:-sbam}"
netbird_if="${NETBIRD_IF:-wt0}"
fail=0

pass() { printf 'PASS  %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }
check() { # description, command...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$what"; else bad "$what"; fi
}

echo "SBAM host check: role=$role host=$(hostname) date=$(date -u +%Y-%m-%dT%H:%MZ)"

# --- Resources ---------------------------------------------------------------------------------
ram_gb=$(awk '/MemTotal/ { printf "%d", $2 / 1024 / 1024 + 0.5 }' /proc/meminfo 2>/dev/null || echo 0)
cpus=$(nproc 2>/dev/null || echo 0)
if [ "$ram_gb" -ge "$min_ram" ]; then pass "RAM ${ram_gb} GB >= ${min_ram} GB"; else bad "RAM ${ram_gb} GB < ${min_ram} GB"; fi
if [ "$cpus" -ge "$min_cpus" ]; then pass "CPUs ${cpus} >= ${min_cpus}"; else bad "CPUs ${cpus} < ${min_cpus}"; fi

# --- Role-specific ------------------------------------------------------------------------------
if [ "$role" = sandbox-host ]; then
  check "/dev/kvm present and usable" test -r /dev/kvm -a -w /dev/kvm
  if command -v kvm-ok >/dev/null 2>&1; then # T03 (Ubuntu: apt install cpu-checker)
    out=$(kvm-ok 2>&1)
    case "$out" in
      *"KVM acceleration can be used"*) pass "kvm-ok: KVM acceleration can be used" ;;
      *) bad "kvm-ok: $out" ;;
    esac
  else
    bad "kvm-ok not installed (apt install cpu-checker)"
  fi
fi
if [ "$role" = dev-vm ]; then
  check "docker installed" command -v docker
  check "docker daemon reachable" docker info
  check "docker compose v2" docker compose version
  # T07: the OpenRouter key file is readable by the service user only.
  if [ -e "$secret_file" ]; then
    perms=$(stat -c '%a %U' "$secret_file")
    if [ "$perms" = "600 $service_user" ]; then
      pass "secret file $secret_file is 600 $service_user"
    else
      bad "secret file $secret_file is '$perms' (want '600 $service_user')"
    fi
  else
    bad "secret file $secret_file missing"
  fi
fi

# --- Network exposure (T04, local half: the external scan is in the runbook) -------------------
check "netbird interface $netbird_if is up" ip link show "$netbird_if"
netbird_ip=$(ip -4 -o addr show "$netbird_if" 2>/dev/null | awk '{ split($4, a, "/"); print a[1] }')
# Listening TCP sockets bound to something other than loopback or the Netbird address are public.
public=$(ss -Htln 2>/dev/null | awk -v nb="$netbird_ip" '{
  addr = $4; sub(/:[0-9]+$/, "", addr); gsub(/[][]/, "", addr)
  if (addr ~ /^127\./ || addr == "::1" || (nb != "" && addr == nb)) next
  print $4
}')
if [ -z "$public" ]; then
  pass "no TCP port listens outside loopback/Netbird"
else
  bad "ports listening on all/public interfaces: $(echo "$public" | tr '\n' ' ')"
fi
if command -v sshd >/dev/null 2>&1; then
  # sshd -T needs root; without it, skip rather than guess.
  if listen=$(sshd -T 2>/dev/null | awk '/^listenaddress/ { print $2 }'); [ -n "$listen" ]; then
    if echo "$listen" | grep -qvE "^(127\.0\.0\.1|\[::1\]|${netbird_ip:-none}):"; then
      bad "sshd listens beyond loopback/Netbird: $(echo "$listen" | tr '\n' ' ')"
    else
      pass "sshd listens on loopback/Netbird only"
    fi
  else
    printf 'SKIP  sshd ListenAddress (run as root to check)\n'
  fi
fi

echo
if [ "$fail" -eq 0 ]; then echo "RESULT: all checks passed"; else echo "RESULT: some checks FAILED"; fi
exit "$fail"
