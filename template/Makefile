# Root orchestration. Each half keeps its own native interface -- pnpm scripts in
# frontend/, uv commands in backend/ -- and this file only fans out to them, so
# there is one place to look from the top and no wrapper to keep in sync.

# Repeated here because uvx and tool invocations run outside project discovery and
# would otherwise miss the setting in backend/pyproject.toml.
UV_EXCLUDE_NEWER ?= 14 days
export UV_EXCLUDE_NEWER

# Where the contract suite points when it runs against the backend half: through the
# dev server, so the run exercises the proxy and the prefix rewrite as well.
CONTRACT_BASE_URL ?= http://localhost:5173/api

.DEFAULT_GOAL := default

.PHONY: default install hooks lint lint-check test test-fast test-contract \
        dev dev-frontend dev-backend openapi openapi-check build upgrade clean

default: install lint test

install:
	pnpm -C frontend install
	cd backend && uv sync --all-groups
	@$(MAKE) --no-print-directory hooks

hooks:
	@sh devtools/install-hooks.sh

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
