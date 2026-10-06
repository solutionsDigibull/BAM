# SBAM developer commands. On Windows run these from Git Bash or WSL2.
# Compose targets (up/down/logs/seed/reset) arrive with E1.10; contract drift and Alembic checks join `check` later.
.PHONY: doctor install lint typecheck test check

doctor:
	bash scripts/doctor.sh

install:
	cd server && uv sync --locked
	pnpm --dir client install --frozen-lockfile

lint:
	cd server && uv run ruff check . && uv run black --check .
	pnpm --dir client lint
	pnpm --dir client format:check

typecheck:
	cd server && uv run mypy
	pnpm --dir client typecheck

test:
	cd server && uv run pytest
	pnpm --dir client test

check: lint typecheck test
	pnpm --dir client build
