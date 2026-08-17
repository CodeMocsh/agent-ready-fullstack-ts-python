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

.PHONY: default install hooks pre-commit lint lint-check test test-fast test-contract \
        test-e2e test-e2e-live dev dev-frontend dev-backend openapi openapi-check \
        build upgrade clean

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
