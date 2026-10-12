#!/bin/sh
# api-migrate entry point (Compose one-off job, runs before sbam-api): `alembic upgrade head` (#36).
# Re-runnable: Alembic skips revisions that are already applied.
set -eu
exec python -m sbam_api.db.migrate upgrade head
