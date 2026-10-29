#!/bin/bash
for f in api jwks hung clamd; do p=$(cat /tmp/audit-poc/live/$f.pid 2>/dev/null); [ -n "$p" ] && kill $p 2>/dev/null; rm -f /tmp/audit-poc/live/$f.pid; done
docker rm -f audit-pg audit-tusd >/dev/null 2>&1; echo down
