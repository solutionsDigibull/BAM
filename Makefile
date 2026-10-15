# SBAM developer commands. On Windows run these from Git Bash or WSL2.
.PHONY: doctor install lint typecheck test check contracts contracts-check migrations-check up ps down logs seed test-int e2e reset runtime-registry compose-lint

# Compose stack (TD §20.3). ENV_FILE can point at another env file, e.g. `make up ENV_FILE=/tmp/ci.env`.
ENV_FILE ?= compose/.env
# Optional profiles on top of core, one file each: `make up PROFILES=obs,sandbox-local` (E1.11).
VALID_PROFILES := obs analytics sandbox-local
comma := ,
PROFILE_LIST := $(subst $(comma), ,$(PROFILES))
ifneq ($(filter-out $(VALID_PROFILES),$(PROFILE_LIST)),)
$(error unknown PROFILES '$(filter-out $(VALID_PROFILES),$(PROFILE_LIST))'; valid profiles: $(VALID_PROFILES))
endif
COMPOSE := docker compose --env-file $(ENV_FILE) -f compose/compose.yaml -f compose/compose.override.yaml 	$(foreach p,$(PROFILE_LIST),-f compose/compose.$(p).yaml)
PROJECT := $(or $(COMPOSE_PROJECT_NAME),sbam)

doctor:
	bash scripts/doctor.sh

install:
	cd server && uv sync --locked
	pnpm --dir client install --frozen-lockfile

lint:
	cd server && uv run ruff check . && uv run black --check .
	cd server && uv run python ../scripts/check_runtime_lock.py  # skill-runtime lock pinned + hashed (#67)
	pnpm --dir client lint
	pnpm --dir client format:check

compose-lint: # deployed envs (dev/staging/prod): digests, ports, secrets, startup order (E16.3, #142); needs docker
	python3 scripts/compose_lint.py

typecheck:
	cd server && uv run mypy
	pnpm --dir client typecheck

test:
	cd server && uv run pytest
	pnpm --dir client test

check: lint typecheck test contracts-check migrations-check
	pnpm --dir client build

# Pydantic -> JSON Schemas (server/contracts/schemas) + TypeScript (client/src/contracts). Commit the result.
contracts:
	cd server && uv run python -m sbam_contracts.gen

# Fails, naming each file, if the committed generated files differ from a fresh generation.
contracts-check:
	cd server && uv run python -m sbam_contracts.gen --check

# Exactly one Alembic head: two parallel migrations must be regenerated, never merged (#36).
migrations-check:
	cd server && test "$$(uv run python -m sbam_api.db.migrate heads | grep -c '(head)')" -eq 1

up:
	@test -f $(ENV_FILE) || { echo "$(ENV_FILE) not found: cp compose/.env.example compose/.env and set the passwords" >&2; exit 1; }
	$(COMPOSE) up -d --build --wait --wait-timeout 900

ps:
	$(COMPOSE) ps

# --remove-orphans also stops profile services started with PROFILES=..., even when PROFILES isn't given here.
down:
	$(COMPOSE) down --remove-orphans

logs:
	$(COMPOSE) logs -f --tail=200 $(s)

seed:
	@echo "seed: nothing to seed yet. The dev realm's users come from the Keycloak import; data seeding lands with #54/#55."

# Integration tests against the running stack (TD §19.3); brings the stack up first if needed.
test-int: up
	cd server && uv run pytest -m integration

e2e:
	pnpm --dir client e2e

# skill-runtime image + its runtime registry, synced into the running stack (#67). Needs `make up`.
runtime-registry:
	docker build -q -f compose/docker/skill-runtime.Dockerfile -t skill-runtime:local .
	docker run --rm --network none skill-runtime:local sbam-runtime-registry \
	  --image-digest "$$(docker image inspect --format '{{.Id}}' skill-runtime:local)" > runtime_registry.json
	cd server && DATABASE_URL="postgresql://sbam:$$(sed -n 's/^SBAM_DB_PASSWORD=//p' ../$(ENV_FILE))@127.0.0.1:$${POSTGRES_PORT:-5432}/sbam" \
	  uv run python -m sbam_api.registry.sync ../runtime_registry.json --activate

# The only destructive target: stops the stack and deletes every volume (database, storage, signatures,
# and the profile volumes such as Langfuse's).
reset:
	@test "$(CONFIRM)" = yes || { echo "reset deletes all stack data. Run: make reset CONFIRM=yes" >&2; exit 1; }
	$(COMPOSE) down -v --remove-orphans
	docker volume ls -q --filter label=com.docker.compose.project=$(PROJECT) | xargs -r docker volume rm
