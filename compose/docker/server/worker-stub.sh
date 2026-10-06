#!/bin/sh
# Placeholder worker so the full Compose core profile runs (TD §20.3.6).
# ponytail: replaced by the Temporal worker bootstrap (`--queues`) in #40 (E1.7).
set -eu
queue="${SBAM_QUEUES:-unset}"
echo "{\"level\":\"INFO\",\"logger\":\"sbam_workers.stub\",\"msg\":\"worker stub idle\",\"queues\":\"${queue}\"}"
mkdir -p /tmp/sbam
trap 'exit 0' TERM INT
while :; do
  touch /tmp/sbam/heartbeat
  sleep 10 &
  wait $!
done
