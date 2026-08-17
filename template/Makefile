# Root orchestration. Each half keeps its own native interface -- pnpm scripts in
# frontend/, uv commands in backend/ -- and this file only fans out to them, so
# there is one place to look from the top and no wrapper to keep in sync.

# bash, not sh: the db-test recipe relies on `trap ... EXIT INT TERM` firing when the
# suite is interrupted, and dash runs the handler only after the foreground command
# returns -- which on Ctrl-C it never does, leaking the container.
SHELL := /bin/bash

# Repeated here because uvx and tool invocations run outside project discovery and
# would otherwise miss the setting in backend/pyproject.toml.
UV_EXCLUDE_NEWER ?= 14 days
export UV_EXCLUDE_NEWER

# Where the contract suite points when it runs against the backend half: through the
# dev server, so the run exercises the proxy and the prefix rewrite as well.
CONTRACT_BASE_URL ?= http://localhost:5173/api

.DEFAULT_GOAL := default

.PHONY: default install hooks pre-commit lint lint-check test test-fast test-contract \
        test-e2e test-e2e-live db-test db db-demo migrate roles schema dev dev-frontend \
        dev-backend openapi \
        openapi-check build upgrade clean

default: install lint test

install:
	pnpm -C frontend install
	cd backend && uv sync --all-groups
	@$(MAKE) --no-print-directory hooks
# Fetches the pinned openapi-typescript into pnpm's dlx store, which is the one thing
# `make pre-commit` needs and neither half's install brings. Doing it here makes
# `make install` the only target that has to reach the network -- without it the first
# commit on a fresh clone goes to the registry, and a commit with no network fails on a
# check that has nothing to do with connectivity. Last, and after the hooks, so that
# losing the registry costs you this line rather than an unarmed clone.
	pnpm -C frontend openapi:types

hooks:
	@sh devtools/install-hooks.sh

# The gate. This one line is what .githooks/pre-commit runs, what CI runs, and the
# place a new check goes -- backend/tests/test_gate.py fails if the three stop
# agreeing. Checks live here rather than in CI because a check here costs seconds
# and the same check discovered on GitHub costs a billed minute plus the round trip
# to find out which of your last four commits broke it.
pre-commit: lint-check openapi-check test

lint:
	pnpm -C frontend lint
	cd backend && uv run python devtools/lint.py

lint-check:
	pnpm -C frontend lint:check
	cd backend && uv run python devtools/lint.py --check

# At this altitude "test" means the system, so it includes the run that needs both
# halves. `test-fast` is the two halves on their own.
test: test-fast test-contract

test-fast:
	pnpm -C frontend test
	cd backend && uv run pytest -q

test-contract:
	@sh devtools/contract-test.sh

# The opt-in tier, and the only checks in this file that are not in the gate. Both
# need a browser binary the gate has no business downloading, and the live one needs
# `make dev` running in another terminal. Opt-in means opt-in, not deleted: if you
# changed UI, this is the run that looks at it, and nothing else will.
test-e2e:
	pnpm -C frontend exec playwright install chromium
	pnpm -C frontend test:e2e

test-e2e-live:
	pnpm -C frontend exec playwright install chromium
	pnpm -C frontend exec playwright test --config playwright.live.config.ts

# The other opt-in member: everything that needs a real PostgreSQL. Also out of the gate,
# and for the same reason -- `make pre-commit` runs on every commit and must not need a
# daemon. Everything about the schema that a fake connection can answer is already in
# `make test` (backend/tests/test_schema.py), including that deploy/schema.sql matches
# app/store/ddl.py, so there is no separate schema-check target to forget to run.
#
# Brings its own database. Set TEST_DATABASE_URL to point at your own instead and this
# target uses it and skips the container entirely.
#
# The container takes an ephemeral host port and a name carrying this shell's PID, because
# a fixed pair breaks both ways when two checkouts run this at once: a taken port fails the
# run outright, and a shared name means one teardown destroys the other's database mid-suite.
DB_TEST_IMAGE ?= postgres:17-alpine
DB_TEST_FILES = tests/test_store_contract.py tests/test_postgres.py tests/test_isolation.py

db-test:
ifdef TEST_DATABASE_URL
	cd backend && uv run --no-sync pytest -rs $(DB_TEST_FILES)
else
	@name=db-test-$$$$; \
	trap 'docker rm -f $$name >/dev/null 2>&1 || true' EXIT INT TERM; \
	docker run -d --rm -P -e POSTGRES_PASSWORD=test --name $$name $(DB_TEST_IMAGE) >/dev/null; \
	port=$$(docker port $$name 5432/tcp | head -1 | sed 's/.*://'); \
	printf 'postgres on %s' "$$port"; \
	until docker exec $$name pg_isready -q >/dev/null 2>&1; do printf '.'; sleep 0.5; done; \
	echo ' ready'; \
	cd backend && TEST_DATABASE_URL=postgres://postgres:test@127.0.0.1:$$port/postgres \
		uv run --no-sync pytest -rs $(DB_TEST_FILES)
endif

# The release step, and the only thing that reads DATABASE_OWNER_URL. The application
# refuses to start if it can see that variable, so this is the one path that can apply
# DDL. Wire it into whatever your platform calls a release command -- see the migrate
# service in deploy/compose.yaml for the shape, and docs/development.md for the mapping.
migrate:
	cd backend && uv run --no-sync python -m app.migrate

# A persistent database to develop against, with the roles applied at init and the schema
# applied the way a release would apply it. Distinct from the throwaway db-test starts.
COMPOSE = deploy/compose.yaml
DB_PORT ?= 5433
DB_SUPERUSER_PASSWORD ?= postgres
APP_DB_PASSWORD ?= app

# compose reads these from its *environment*, and a make variable is not one: a
# `make db DB_PORT=5544` that reached only the migration would start the database on 5433 and
# then migrate whatever was already listening on 5544. `export` rather than a VAR=value prefix
# on the recipe line, because a prefix goes through the shell and a password containing a
# quote or a space would end the command rather than be passed to it.
export DB_PORT
export APP_DB_PASSWORD
export POSTGRES_PASSWORD = $(DB_SUPERUSER_PASSWORD)

db:
	docker compose -f $(COMPOSE) up -d --wait postgres
	@cd backend && DATABASE_OWNER_URL=postgres://postgres:$(DB_SUPERUSER_PASSWORD)@localhost:$(DB_PORT)/postgres \
		uv run --no-sync python -m app.migrate
	@echo ""
# The printed URL assumes APP_DB_PASSWORD needs no percent-encoding, which the default does
# not. It is a convenience for a local development database; a real deployment sets
# DATABASE_URL itself and never comes through here.
	@echo "export DATABASE_URL=postgres://app_app:$(APP_DB_PASSWORD)@localhost:$(DB_PORT)/postgres"
	@echo "# then: make dev-backend. That role holds DML and no CREATE, so the app verifies"
	@echo "# the schema and cannot apply it -- run 'make migrate' when you add an entry."

# Isolation, made visible. Everything else about it is proved by tests nobody watches.
db-demo:
	@cd backend && DATABASE_URL=$${DATABASE_URL:-postgres://app_app:$(APP_DB_PASSWORD)@localhost:$(DB_PORT)/postgres} \
		uv run --no-sync python devtools/isolation_demo.py

# deploy/schema.sql and deploy/roles.sql are generated from app/store/, the way
# openapi.json is generated from the models. backend/tests/test_schema.py fails when either
# committed file drifts, so this is how you make that test pass after a change.
schema:
	cd backend && uv run python devtools/schema.py ../deploy/schema.sql

roles:
	cd backend && uv run python devtools/roles.py ../deploy/roles.sql

dev:
	@sh devtools/dev.sh

dev-frontend:
	pnpm -C frontend dev

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

openapi:
	cd backend && uv run python devtools/export_openapi.py ../openapi.json
	pnpm -C frontend openapi:types

openapi-check: openapi
	@git diff --exit-code -- openapi.json frontend/src/api/schema.ts \
	  || { echo "" >&2; \
	       echo "The contract artifacts do not match the backend code." >&2; \
	       echo "Run 'make openapi' and commit the result." >&2; exit 1; }

build:
	pnpm -C frontend build

# app.openapi() is stable for pinned versions but not across FastAPI or pydantic
# minor bumps, and openapi-typescript changes its own output, so an upgrade that
# skipped this would leave the committed artifacts stale.
upgrade:
	pnpm -C frontend update --latest
	cd backend && uv sync --upgrade --all-groups
	@$(MAKE) --no-print-directory openapi

clean:
	-rm -rf frontend/dist frontend/coverage frontend/test-results frontend/playwright-report
	-rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache
	-find backend -type d -name "__pycache__" -exec rm -rf {} +
