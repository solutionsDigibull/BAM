#!/bin/sh
# api-migrate entry point (Compose one-off job, runs before sbam-api).
# ponytail: stub until #36 (E1.5) adds Alembic; it then becomes `exec alembic upgrade head`.
set -eu
echo '{"level":"INFO","logger":"sbam_api.migrate","msg":"no migrations yet (Alembic arrives with #36)"}'
