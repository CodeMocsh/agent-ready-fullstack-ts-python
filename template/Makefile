SHELL := /bin/bash

UV_EXCLUDE_NEWER ?= 7 days
export UV_EXCLUDE_NEWER

.DEFAULT_GOAL := default

.PHONY: default install hooks pre-commit gate secrets lint lint-check test test-fast test-contract \
        test-e2e test-e2e-live db-test test-contract-db db db-demo migrate roles schema \
        dev dev-frontend observe observe-test \
        dev-backend openapi \
        openapi-check build upgrade clean

default: install lint test

install:
	pnpm -C frontend install
	cd backend && uv sync --all-groups
	@$(MAKE) --no-print-directory hooks
	sh devtools/secrets.sh fetch
	pnpm -C frontend openapi:types

hooks:
	@sh devtools/install-hooks.sh

pre-commit:
	@sh devtools/gate.sh

gate: secrets lint-check openapi-check test

secrets:
	@sh devtools/secrets.sh

lint:
	pnpm -C frontend lint
	cd backend && uv run python devtools/lint.py

lint-check:
	pnpm -C frontend lint:check
	cd backend && uv run python devtools/lint.py --check

test: test-fast test-contract

test-fast:
	pnpm -C frontend test
	cd backend && uv run pytest -q

test-contract:
	@sh devtools/contract-test.sh

test-e2e:
	pnpm -C frontend exec playwright install chromium
	pnpm -C frontend test:e2e

test-e2e-live:
	pnpm -C frontend exec playwright install chromium
	pnpm -C frontend exec playwright test --config playwright.live.config.ts

DB_TEST_IMAGE ?= postgres:17-alpine
DB_TEST_SUITE = tests/integration

db-test:
ifdef TEST_DATABASE_URL
	cd backend && uv run --no-sync pytest $(DB_TEST_SUITE)
else
	@name=db-test-$$$$; \
	trap 'docker rm -f $$name >/dev/null 2>&1 || true' EXIT INT TERM; \
	docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=test --name $$name $(DB_TEST_IMAGE) >/dev/null; \
	port=$$(docker port $$name 5432/tcp | head -1 | sed 's/.*://'); \
	printf 'postgres on %s' "$$port"; \
	until docker exec $$name pg_isready -h 127.0.0.1 -q >/dev/null 2>&1; do printf '.'; sleep 0.5; done; \
	echo ' ready'; \
	cd backend && TEST_DATABASE_URL=postgres://postgres:test@127.0.0.1:$$port/postgres \
		uv run --no-sync pytest $(DB_TEST_SUITE)
endif

OBSERVE_IMAGE = $(shell sed -n 's/^ *image: \(grafana\/otel-lgtm:.*\)$$/\1/p' $(COMPOSE))
OBSERVE_TEST_SUITE = tests/observe

observe-test:
	@name=observe-test-$$$$; \
	trap 'docker rm -f $$name >/dev/null 2>&1 || true' EXIT INT TERM; \
	docker run -d --rm -p 127.0.0.1::3000 -p 127.0.0.1::4318 --name $$name $(OBSERVE_IMAGE) >/dev/null; \
	grafana=http://$$(docker port $$name 3000/tcp | head -1); \
	otlp=http://$$(docker port $$name 4318/tcp | head -1); \
	printf 'otel-lgtm on %s' "$$grafana"; \
	until docker exec $$name test -f /tmp/ready 2>/dev/null; do printf '.'; sleep 1; done; \
	echo ' ready'; \
	cd backend && OBSERVE_GRAFANA_URL=$$grafana OBSERVE_OTLP_URL=$$otlp \
		uv run --no-sync pytest $(OBSERVE_TEST_SUITE)

test-contract-db:
	@name=contract-db-$$$$; \
	trap 'docker rm -f $$name >/dev/null 2>&1 || true' EXIT INT TERM; \
	docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=test --name $$name $(DB_TEST_IMAGE) >/dev/null; \
	port=$$(docker port $$name 5432/tcp | head -1 | sed 's/.*://'); \
	printf 'postgres on %s' "$$port"; \
	until docker exec $$name pg_isready -h 127.0.0.1 -q >/dev/null 2>&1; do printf '.'; sleep 0.5; done; \
	echo ' ready'; \
	docker exec -i $$name psql -q -v ON_ERROR_STOP=1 -U postgres < deploy/roles.sql; \
	docker exec $$name psql -q -v ON_ERROR_STOP=1 -U postgres \
		-c "ALTER ROLE app_app PASSWORD 'test'" >/dev/null; \
	(cd backend && DATABASE_OWNER_URL=postgres://postgres:test@127.0.0.1:$$port/postgres \
		uv run --no-sync python -m app.migrate); \
	DATABASE_URL=postgres://app_app:test@127.0.0.1:$$port/postgres \
		sh devtools/contract-test.sh

migrate:
	cd backend && uv run --no-sync python -m app.migrate

COMPOSE = deploy/compose.yaml
DB_PORT ?= 5433
DB_SUPERUSER_PASSWORD ?= postgres
APP_DB_PASSWORD ?= app
OBSERVE_PORT ?= 3000
OTLP_PORT ?= 4318

export DB_PORT
export OBSERVE_PORT
export OTLP_PORT
export APP_DB_PASSWORD
export POSTGRES_PASSWORD = $(DB_SUPERUSER_PASSWORD)

db:
	docker compose -f $(COMPOSE) up -d --wait postgres
	@cd backend && DATABASE_OWNER_URL=postgres://postgres:$(DB_SUPERUSER_PASSWORD)@127.0.0.1:$(DB_PORT)/postgres \
		uv run --no-sync python -m app.migrate
	@echo ""
	@echo "export DATABASE_URL=postgres://app_app:$(APP_DB_PASSWORD)@127.0.0.1:$(DB_PORT)/postgres"
	@echo "# then: make dev-backend. That role holds DML and no CREATE, so the app verifies"
	@echo "# the schema and cannot apply it -- run 'make migrate' when you add an entry."

observe:
	docker compose -f $(COMPOSE) --profile observe up -d --wait observe
	@echo ""
	@echo "export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$(OTLP_PORT) OTEL_SERVICE_NAME=api"
	@echo "# then: make dev. Grafana is on http://127.0.0.1:$(OBSERVE_PORT) -- Explore, then Tempo"
	@echo "# for traces and Prometheus for http_server_request_duration_seconds."

db-demo:
	@cd backend && DATABASE_URL=$${DATABASE_URL:-postgres://app_app:$(APP_DB_PASSWORD)@127.0.0.1:$(DB_PORT)/postgres} \
		uv run --no-sync python devtools/isolation_demo.py

schema:
	cd backend && uv run python devtools/schema.py ../deploy/schema.sql

roles:
	cd backend && uv run python devtools/roles.py ../deploy/roles.sql

dev:
	@sh devtools/dev.sh

dev-frontend:
	pnpm -C frontend dev

dev-backend:
	cd backend && APP_ENV=development PYTHONASYNCIODEBUG=1 uv run uvicorn app.main:app --reload --port 8000

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

upgrade:
	pnpm -C frontend update --latest
	cd backend && uv sync --upgrade --all-groups
	@$(MAKE) --no-print-directory openapi

clean:
	-rm -rf frontend/dist frontend/coverage frontend/test-results frontend/playwright-report
	-rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache
	-find backend -type d -name "__pycache__" -exec rm -rf {} +
