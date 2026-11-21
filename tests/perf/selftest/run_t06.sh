#!/usr/bin/env bash
# E15.4-T06 (#138) "Threshold breach fails job": inject 800 ms latency, expect k6 to exit non-zero.
#
#   bash tests/perf/selftest/run_t06.sh          (needs k6 on PATH, or K6=/path/to/k6)
#
# Three runs against tests/perf/selftest/slow_server.py on 127.0.0.1:
#   1. T01 at 800 ms   -> must exit 99 (k6's "thresholds crossed" code): p95 > 500 ms.
#   2. T01 at 0 ms     -> must exit 0: the control, so (1) fails for latency, not for a broken setup.
#   3. T02 at 2500 ms  -> must exit 99: grid p95 > 2000 ms.
# Exits 0 only when all three behave. Synthetic data; nothing leaves 127.0.0.1.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PERF="$(cd "$HERE/.." && pwd)"
K6="${K6:-k6}"
PORT="${PORT:-8765}"
PY="${PYTHON:-python}"

if ! command -v "$K6" >/dev/null 2>&1; then
  echo "run_t06: k6 not found (set K6=/path/to/k6). Self-test NOT run." >&2
  exit 2
fi

SERVER_PID=""
stop_server() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""; }
trap stop_server EXIT

start_server() { # $1 = latency ms
  "$PY" "$HERE/slow_server.py" --port "$PORT" --latency-ms "$1" &
  SERVER_PID=$!
  for _ in $(seq 1 50); do
    "$PY" -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$PORT)))" && return 0
    sleep 0.1
  done
  echo "run_t06: slow_server did not start" >&2; exit 2
}

ENV=(-e BASE_URL="http://127.0.0.1:$PORT" -e TOKEN=selftest
     -e PROJECT_ID=00000000-0000-4000-8000-000000000001 -e WORKFLOW_ID=00000000-0000-4000-8000-000000000002)
FAIL=0

expect() { # $1 = name, $2 = expected exit code, $3 = latency ms, rest = k6 args
  local name="$1" want="$2" latency="$3"; shift 3
  start_server "$latency"
  "$K6" run --quiet "${ENV[@]}" "$@"
  local got=$?
  stop_server
  if [ "$got" -eq "$want" ]; then echo "run_t06: PASS $name (exit $got)"; else echo "run_t06: FAIL $name (exit $got, wanted $want)"; FAIL=1; fi
}

expect "T01 breach at 800 ms" 99 800 -e VUS=2 -e DURATION=10s -e THINK_S=0.1 "$PERF/scenarios/t01-interactive.js"
expect "T01 control at 0 ms" 0 0 -e VUS=2 -e DURATION=5s -e THINK_S=0.1 "$PERF/scenarios/t01-interactive.js"
expect "T02 breach at 2500 ms" 99 2500 -e ITERATIONS=3 "$PERF/scenarios/t02-grid.js"
exit $FAIL
