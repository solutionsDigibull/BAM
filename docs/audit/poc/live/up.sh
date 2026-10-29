#!/bin/bash
# Bring up the throwaway audit stack: postgres, migrations, JWKS, API (PID files in /tmp/audit-poc/live)
set -e
cd "/home/Fool/Arch - Work/BAM-audit/server"; source /tmp/audit-poc/live/env.sh
docker rm -f audit-pg >/dev/null 2>&1 || true
docker run -d --name audit-pg -e POSTGRES_PASSWORD=auditroot -p 127.0.0.1:55999:5432 postgres:16.15-alpine >/dev/null
for i in $(seq 1 30); do docker exec audit-pg pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done; sleep 2
docker exec audit-pg psql -U postgres -c "CREATE ROLE sbam LOGIN PASSWORD 'auditpw'" -c "CREATE DATABASE sbam OWNER sbam" >/dev/null
.venv/bin/python -I -m sbam_api.db.migrate >/dev/null 2>&1
nohup .venv/bin/python -I /tmp/audit-poc/live/jwks_server.py >/tmp/audit-poc/live/jwks.log 2>&1 & echo $! > /tmp/audit-poc/live/jwks.pid
export CLAMAV_HOST=127.0.0.1 CLAMAV_PORT=${CLAMAV_PORT:-1}
nohup .venv/bin/python -I -m uvicorn sbam_api.main:create_app --factory --host 127.0.0.1 --port 55997 > /tmp/audit-poc/live/api.log 2>&1 & echo $! > /tmp/audit-poc/live/api.pid
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:55997/health/live && break; sleep 1; done
echo up
