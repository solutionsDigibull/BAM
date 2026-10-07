# SBAM developer commands. On Windows run these from Git Bash or WSL2.
# Compose targets (up/down/logs/seed/reset) arrive with E1.10; the Alembic check joins `check` later.
.PHONY: doctor install lint typecheck test check contracts contracts-check

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

check: lint typecheck test contracts-check
	pnpm --dir client build

# Pydantic -> JSON Schemas (server/contracts/schemas) + TypeScript (client/src/contracts). Commit the result.
contracts:
	cd server && uv run python -m sbam_contracts.gen

# Fails, naming each file, if the committed generated files differ from a fresh generation.
contracts-check:
	cd server && uv run python -m sbam_contracts.gen --check
