#!/bin/sh
# Render the template and exercise the result. One script, three callers: CI, the
# pre-commit hook, and your laptop. When these steps lived only in the workflow file
# they could only run on GitHub, and a check that cannot run locally is a check
# people learn to discover late.
#
# Usage: check_template.sh [default|proprietary|no-license]
#        FAST=1 check_template.sh   -- render and assert only, skipping the installs
set -eu

# Git exports GIT_DIR and friends into the environment of the hooks it runs. This
# script renders into temp directories and runs git inside them, so an inherited git
# context silently points that work at the calling repository.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

VARIANT="${1:-default}"
FAST="${FAST:-0}"
COPIER_SPEC="copier@9.16.0"
export UV_EXCLUDE_NEWER="14 days"

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# `set -e` makes a bare `test -f` exit silently, which turns a one-line problem into
# a bisect. These say what they were looking for.
fail() { echo "check: $*" >&2; exit 1; }
need() { [ -f "$1" ] || fail "expected file: $1"; }
need_exec() { [ -x "$1" ] || fail "expected executable file: $1"; }
need_absent() { [ ! -e "$1" ] || fail "expected no such file: $1"; }
need_grep() { grep -q "$1" "$2" || fail "expected /$1/ in $2"; }
need_no_grep() { ! grep -q "$1" "$2" || fail "unexpected /$1/ in $2"; }


for tool in uvx uv tar python3 git node pnpm curl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "check: $tool is required and not on PATH." >&2
        echo "check: see template/docs/installation.md for how to install it." >&2
        exit 1
    fi
done

case "$VARIANT" in
    default)      EXTRA="" ;;
    proprietary)  EXTRA="--data package_license=Proprietary" ;;
    no-license)   EXTRA="--data package_license=None" ;;
    *) echo "check: unknown variant '$VARIANT' (default|proprietary|no-license)" >&2; exit 2 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM
SRC="$WORK/src"
OUT="$WORK/out"
mkdir -p "$SRC"

echo "==> render ($VARIANT)"
# Staged through a file rather than a pipe on purpose: in a pipeline only the last
# command's status reaches "set -e", so a failed archive step would still extract
# whatever it managed to write and the run would go on against a partial tree --
# passing checks on a template that is not the one on disk. "set -o pipefail" is the
# usual fix but is not in POSIX sh, and this script runs under dash on Linux CI,
# where that line is itself an error.
tar --exclude=.git --exclude=node_modules --exclude=.venv \
    -cf "$WORK/tree.tar" -C "$REPO" .
tar -xf "$WORK/tree.tar" -C "$SRC"
rm -f "$WORK/tree.tar"

# Users generate from a git URL, and Copier records the template commit in the
# answers file so `copier update` knows where it started. Rendering the working-tree
# copy as a plain directory would skip that path entirely, so commit it first.
git -C "$SRC" init -q -b main
git -C "$SRC" -c user.email=check@example.com -c user.name=check \
    -c commit.gpgsign=false add -A
git -C "$SRC" -c user.email=check@example.com -c user.name=check \
    -c commit.gpgsign=false commit -qm "working tree under test"

uvx --exclude-newer "14 days" "$COPIER_SPEC" copy --defaults --quiet --trust \
    --vcs-ref=HEAD \
    --data package_name=smoke-test \
    --data package_description="A smoke test project" \
    --data package_author_name="Test Author" \
    --data package_author_email=test@example.com \
    --data package_github_org=testorg \
    $EXTRA \
    "$SRC" "$OUT"

cd "$OUT"

echo "==> assert the agent-ready layer"
need AGENTS.md
need CLAUDE.md
need_grep '@AGENTS.md' CLAUDE.md
need .claude/settings.json
python3 -c "import json,sys; json.load(open('.claude/settings.json'))"
need .claude/hooks/agent_guard.py
python3 -m py_compile .claude/hooks/agent_guard.py
need .entire/settings.json
python3 -c "import json,sys; json.load(open('.entire/settings.json'))"
need docs/agent-tooling.md
# AGENTS.md sends every rationale that is not a commit message here, so the directory
# has to exist in a fresh project rather than being somewhere an agent is told to
# write and finds missing.
need docs/adr/README.md
need .copier-answers.yml
need_grep '_src_path' .copier-answers.yml
need_grep '_commit' .copier-answers.yml
# The executable bit is real state and rendering can drop it. A hook that is not
# executable is a hook that silently never runs.
need_exec .githooks/pre-commit
need_exec devtools/install-hooks.sh
need_exec devtools/dev.sh
need_exec devtools/contract-test.sh

echo "==> assert nothing was left unrendered"
# The [^$] excludes GitHub Actions' ${{ }}, which is not a Copier token.
if grep -rEn '(^|[^$])\{\{ *[a-z_]+ *\}\}' . 2>/dev/null; then
    echo "unrendered token above: a file carrying tokens needs the .jinja suffix" >&2
    exit 1
fi
if grep -rn '{%' . 2>/dev/null; then
    echo "unrendered Jinja statement above" >&2
    exit 1
fi
if [ -n "$(find . -name '*.jinja' 2>/dev/null)" ]; then
    echo "a .jinja file survived rendering: $(find . -name '*.jinja')" >&2
    exit 1
fi

if [ "$VARIANT" = "default" ]; then
    echo "==> assert hostile answers stay data, not syntax"
    # Four of the six questions are free-form prose, and two of the files they land
    # in are hand-written JSON and TOML. An answer interpolated raw takes its quotes
    # and backslashes with it, and the result is a manifest no tool can parse -- a
    # failure that surfaces in someone else's project, on `pnpm install`, with
    # nothing in the diff to suggest why. This renders what a person could plausibly
    # type and parses both manifests back. It runs once: nothing here varies with
    # the license.
    HOSTILE="$WORK/hostile"
    H_DESC='A "quoted" project: Bob'"'"'s tasks & <friends>, C:\notes'
    H_AUTHOR='O'"'"'Brien "Bob" \ Tester'
    H_EMAIL="o'brien@example.com"
    H_ORG='test"org'
    export H_DESC H_AUTHOR H_EMAIL H_ORG
    uvx --exclude-newer "14 days" "$COPIER_SPEC" copy --defaults --quiet --trust \
        --vcs-ref=HEAD \
        --data package_name=smoke-test \
        --data package_description="$H_DESC" \
        --data package_author_name="$H_AUTHOR" \
        --data package_author_email="$H_EMAIL" \
        --data package_github_org="$H_ORG" \
        "$SRC" "$HOSTILE"
    python3 - "$HOSTILE" <<'PY'
import json
import os
import sys
import tomllib

out = sys.argv[1]
desc = os.environ["H_DESC"]
author = os.environ["H_AUTHOR"]
email = os.environ["H_EMAIL"]
org = os.environ["H_ORG"]

FIX = """
check: every free-form answer must go through the q() macro that package.json.jinja
       and pyproject.toml.jinja each define at the top -- `{{ q(package_description) }}`,
       not `"{{ package_description }}"` -- including answers concatenated with
       literal text. Raw interpolation carries the user's quotes and backslashes
       into the manifest as syntax.
"""


def die(message):
    print(f"check: {message}", file=sys.stderr)
    print(FIX, file=sys.stderr)
    raise SystemExit(1)


try:
    with open(f"{out}/frontend/package.json", encoding="utf-8") as handle:
        pkg = json.load(handle)
except ValueError as error:
    die(f"the rendered frontend/package.json is not valid JSON: {error}")
try:
    with open(f"{out}/backend/pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
except tomllib.TOMLDecodeError as error:
    die(f"the rendered backend/pyproject.toml is not valid TOML: {error}")

# Parsing is half the check: a value can survive it and still come out mangled, so
# every answer is compared against what went in.
for label, got, want in (
    ("package.json description", pkg["description"], desc),
    ("package.json author", pkg["author"], {"name": author, "email": email}),
    ("package.json repository url", pkg["repository"]["url"],
     f"git+https://github.com/{org}/smoke-test.git"),
    ("pyproject description", project["description"], f"{desc} -- backend"),
    ("pyproject authors", project["authors"], [{"name": author, "email": email}]),
):
    if got != want:
        die(f"{label} came out as {got!r}, not {want!r}")
PY
fi

echo "==> assert the agent guard"
# The guard is a heuristic backstop, and both directions of the heuristic have a
# cost: a miss lets a catastrophic command through, and a false hit teaches agents
# that the guard is noise and worth routing around. One case cannot show either, so
# assert a table. These live here rather than in the generated project's tests
# because they check the template's own logic, not the user's.
guard_denies() {
    printf '{"tool_name":"Bash","tool_input":{"command":"%s"}}' "$1" \
        | python3 .claude/hooks/agent_guard.py \
        | grep -q '"permissionDecision": "deny"'
}

MUST_DENY="rm -rf /
rm -r -f /
rm -fr /
rm --recursive --force /
rm / -rf
rm -rf -- /
rm -rf --no-preserve-root /
rm -rf \$HOME
rm -rf \${HOME}
rm -rf ~
rm -rf /*
:(){ :|:& };:
git push --force origin main
git commit --no-verify -m wip"

MUST_ALLOW="rm -rf ./build
rm -rf frontend/node_modules
rm -rf backend/.venv
rm -rf /tmp/smoke-test
rm -r ./dist
rm -f ./notes.txt
ls -rf /
ls -la
git push origin main
git push --force-with-lease origin feature"

echo "$MUST_DENY" | while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    if ! guard_denies "$cmd"; then
        echo "the agent guard allowed a command it must deny: $cmd" >&2
        exit 1
    fi
done

echo "$MUST_ALLOW" | while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    if guard_denies "$cmd"; then
        echo "the agent guard denied a command it must allow: $cmd" >&2
        exit 1
    fi
done

echo "==> assert the shape of both halves"
need Makefile
for target in install hooks pre-commit lint lint-check test test-fast test-contract \
              test-e2e test-e2e-live db-test db db-demo migrate roles schema dev \
              dev-frontend dev-backend \
              openapi openapi-check build upgrade clean; do
    grep -q "^$target:" Makefile || { echo "Makefile is missing the $target target" >&2; exit 1; }
done
# The gate is one named list so the hook, CI and a laptop cannot drift apart, and the
# generated project's own test_gate.py is what keeps them together from then on. Here
# we only assert it exists and that the browser tier stayed out of it: a gate that
# downloads a browser is a gate people learn to commit around.
need_grep '^pre-commit: lint-check openapi-check test$' Makefile
need_no_grep '^pre-commit:.*test-e2e' Makefile
need_no_grep '^pre-commit:.*db-test' Makefile
need_no_grep '^pre-commit:.*test-contract-db' Makefile
# The contract suite on Postgres serves as the application role, never as the superuser: a
# superuser bypasses every policy, so the admin connection would pass against a database
# carrying no isolation at all.
need_grep 'DATABASE_URL=postgres://app_app' Makefile
need backend/tests/test_gate.py
# The db-test recipe traps on INT so an interrupted suite does not leak its container, and
# `trap ... EXIT INT TERM` only fires on Ctrl-C under bash -- dash runs the handler after the
# foreground command returns, which on Ctrl-C it never does.
need_grep '^SHELL := /bin/bash' Makefile
# CI builds and tests; it does not deploy, and it does not publish.
test "$(ls .github/workflows)" = "ci.yml"

# `make dev` has to stop what it started. Job control puts the backend in a process
# group of its own, so a Ctrl-C in the terminal reaches only the script -- and the
# script's cleanup trap is the one thing that then kills the backend. Exec'ing the
# frontend replaces the shell and discards that trap, stranding uvicorn on :8000
# after vite is gone. That shipped once. Nothing else here runs dev.sh, so these
# three lines are the only thing standing between a one-word edit and the bug.
need_no_grep 'exec pnpm' devtools/dev.sh
need_grep 'trap cleanup' devtools/dev.sh
need_grep 'set -m' devtools/dev.sh
# The ports are read from the environment in all three places rather than hard-coded in
# each, so two checkouts can run at once and the proxy still points at the backend the
# script started. Hard-coding meant a second worktree's `make dev` took the first one's
# port, and this script refuses to run at all while anything else holds either.
need_grep 'BACKEND_PORT' devtools/dev.sh
need_grep 'FRONTEND_PORT' devtools/dev.sh
need_grep 'BACKEND_PORT' devtools/contract-test.sh
need_grep 'process.env.BACKEND_PORT' frontend/vite.config.ts

need frontend/index.html
need frontend/vite.config.ts
need frontend/src/main.tsx
need frontend/src/router.tsx
need frontend/components.json
need frontend/public/mockServiceWorker.js
for mock in handlers store browser node; do
    need "frontend/src/mocks/$mock.ts"
done
need_grep 'VITE_ENABLE_MSW=true' frontend/.env.development
need_grep 'VITE_ENABLE_MSW=true' frontend/.env.mock
need_grep 'VITE_ENABLE_MSW=false' frontend/.env.live
need_grep '"private": true' frontend/package.json
# Subpath safety: none of these may hard-code a leading slash.
need_grep 'BASE_URL}mockServiceWorker.js' frontend/src/main.tsx
need_grep 'basepath: import.meta.env.BASE_URL' frontend/src/router.tsx
need_grep 'import.meta.env.BASE_URL' frontend/src/api/base.ts

need backend/pyproject.toml
need backend/app/main.py
need backend/app/models.py
need backend/app/routes.py
need backend/app/deps.py
need backend/app/wiring.py
need backend/tests/test_tasks.py
# Two substrates behind one contract, and one suite over both. A store package with only
# one implementation in it is a shape nothing checks -- see docs/adr/0001.
need backend/app/store/__init__.py
need backend/app/store/memory.py
need backend/app/store/pg.py
need backend/app/store/ddl.py
need backend/app/store/migrate.py
need backend/app/store/conn.py
need backend/tests/test_store_contract.py
need backend/tests/test_schema.py
need backend/tests/test_postgres.py
need docs/adr/0001-two-substrates-behind-one-contract.md
need_absent backend/app/store.py
# The generated schema, committed the way openapi.json is. backend/tests/test_schema.py
# fails when it drifts, and that test is in the fast tier, so the gate catches it.
need deploy/schema.sql
need deploy/roles.sql
need deploy/compose.yaml
need_exec deploy/credentials.sh
need CONTEXT.md
for adr in 0002-tenant-isolation-is-forced-and-always-on \
           0003-the-application-never-applies-ddl \
           0004-the-schema-and-the-binary-must-match; do
    need "docs/adr/$adr.md"
done

echo "==> assert tenant isolation is wired, not just described"
# FORCE, not ENABLE. A table's owner bypasses its own policies by default, so with ENABLE
# alone the role that applied the schema reads every row -- silently, with nothing anywhere
# reporting a problem. backend/tests/test_isolation.py proves it against a real server; this
# catches the edit before anyone has a database to run that against.
need_grep 'FORCE ROW LEVEL SECURITY' deploy/schema.sql
need_grep 'ENABLE ROW LEVEL SECURITY' deploy/schema.sql
# WITH CHECK as well as USING, or a tenant may insert a row it cannot then read.
need_grep 'WITH CHECK' deploy/schema.sql
# The constraint that makes "an unset tenant matches nothing" a mechanism rather than an
# observation about the data: without it one row with an empty tenant is readable by every
# connection that never set one.
need_grep "CHECK (tenant_id <> '')" deploy/schema.sql
# Every index on a tenant table leads with tenant_id, or the policy cannot use it.
need_grep 'ON tasks (tenant_id,' deploy/schema.sql
# The application refuses to hold a credential that could apply DDL.
need_grep 'DATABASE_OWNER_URL' backend/app/migrate.py
need_grep 'OwnerCredentialVisible' backend/app/wiring.py
need_no_grep 'DATABASE_OWNER_URL' backend/app/store/pg.py
# The migrate service waits for a database that is genuinely up, and the file says how an
# application would in turn wait for the migration to *finish* rather than to start.
need_grep 'condition: service_healthy' deploy/compose.yaml
need_grep 'service_completed_successfully' deploy/compose.yaml
# Over TCP, not the socket: the entrypoint runs init against a temporary server that answers
# on the unix socket only, so a socket probe reports healthy while init is still running.
need_grep 'pg_isready -h 127.0.0.1' deploy/compose.yaml
need backend/devtools/schema.py
need_grep 'CREATE TABLE IF NOT EXISTS tasks' deploy/schema.sql
need_grep 'applied_once' deploy/schema.sql
# The search path is a startup parameter, never a statement: asyncpg's RESET ALL on release
# restores startup parameters and clears session SETs, so a `SET search_path` from a connect
# hook survives exactly one checkout and every one after it resolves against public --
# silently, because the first query on each connection works.
# The behavioural guard is backend/tests/test_postgres.py, which fails with `public` if
# this changes; this only asserts the mechanism is still the one that test is about.
need_grep 'server_settings' backend/app/store/pg.py
need backend/devtools/export_openapi.py
need_grep '^3.12' backend/.python-version
# An application, not a library: no build backend, no wheel, nothing published.
if grep -q '^\[build-system\]' backend/pyproject.toml; then
    echo "backend/pyproject.toml declares a build system; this half is an application" >&2
    exit 1
fi
need_grep 'package = false' backend/pyproject.toml
need_grep 'pythonpath' backend/pyproject.toml

echo "==> assert the contract"
need openapi.json
need frontend/src/api/schema.ts
python3 -c "
import json
spec = json.load(open('openapi.json'))
assert spec['openapi'].startswith('3.1'), spec['openapi']
schemas = spec['components']['schemas']
for name in ('Task', 'CreateTaskBody', 'UpdateTaskBody', 'ErrorBody'):
    assert name in schemas, f'{name} missing from the spec: {sorted(schemas)}'
# separate_input_output_schemas=False keeps these names stable; the aliases in
# src/api/types.ts import them by name and break the moment they split.
split = [n for n in schemas if n.endswith('-Input') or n.endswith('-Output')]
assert not split, f'input/output schemas split: {split}'
paths = spec['paths']
assert sorted(paths) == ['/tasks', '/tasks/{id}'], sorted(paths)
assert '201' in paths['/tasks']['post']['responses']
assert '204' in paths['/tasks/{id}']['delete']['responses']
# An error response without a model claims it has no body, while HTTPException
# returns one -- and the typed handlers then cannot mock it.
for method in ('patch', 'delete'):
    assert 'content' in paths['/tasks/{id}'][method]['responses']['404'], method
"
# The proxy strips the prefix, so the spec must not carry it.
need_grep '"/tasks"' openapi.json
need_grep 'rewrite' frontend/vite.config.ts
need_grep 'redirect_slashes = False' backend/app/main.py
need_grep 'separate_input_output_schemas=False' backend/app/main.py
# The generator needs its own TypeScript; the frontend is on a major where the
# compiler API it uses no longer exists.
need_grep 'pnpm dlx openapi-typescript@' frontend/package.json
need frontend/tests/contract.test.ts
need_grep 'CONTRACT_TARGET' frontend/tests/contract.test.ts
# Starting the mock worker during the live run would intercept the very requests
# that run exists to make, and the suite would pass while proving nothing.
need_grep 'CONTRACT_TARGET' frontend/tests/setup.ts
# The contract suite is the only check that can fail on the two halves not
# interoperating, and it reaches the hook through the gate rather than by name.
need_grep 'make -s pre-commit' .githooks/pre-commit
need_grep 'test-contract' Makefile
need_grep 'make test-contract' .github/workflows/ci.yml
# That job is the one place the single-toolchain rule is broken on purpose, and it
# needs both setups to be right: pnpm cannot find a version without being pointed at
# the half's package.json, and uv below the floor rejects the relative cool-off.
python3 - <<'PY'
import re

workflow = open(".github/workflows/ci.yml", encoding="utf-8").read()
job = re.search(r"^  contract:\n(  .*\n|\n)*", workflow, re.M)
assert job is not None, "the generated workflow has no contract job"
body = job.group(0)
assert "package_json_file: frontend/package.json" in body, body
assert 'version: "0.11.25"' in body, body
PY

echo "==> assert the quality gates"
need_grep 'noExcessiveCognitiveComplexity' frontend/biome.json
need_grep 'noExcessiveLinesPerFunction' frontend/biome.json
need_grep 'noExcessiveLinesPerFile' frontend/biome.json
for rule in noUselessElse useCollapsedElseIf noNegationElse useSimplifiedLogicExpression; do
    grep -q "\"$rule\"" frontend/biome.json || { echo "verbosity rule $rule missing" >&2; exit 1; }
done
need frontend/devtools/complexity.mjs
need frontend/devtools/conformance.mjs
need_grep 'devtools/complexity.mjs' frontend/package.json
need_grep 'devtools/conformance.mjs' frontend/package.json
need_grep '"strict": true' frontend/tsconfig.json
need_grep 'noUncheckedIndexedAccess' frontend/tsconfig.json
# The generated contract artifact is a normal .ts file that grows with the API, so
# every automatic skip misses it: measuring or linting it would distort the baseline
# and fail on code nobody wrote.
node -e "
const pkg = require('./frontend/package.json');
for (const key of ['complexity', 'conformance', 'comments']) {
  if (!pkg[key].exclude.includes('src/api/schema.ts')) {
    throw new Error(key + '.exclude is missing src/api/schema.ts');
  }
}
const biome = require('./frontend/biome.json');
if (!biome.files.includes.includes('!src/api/schema.ts')) {
  throw new Error('biome.json does not exclude src/api/schema.ts');
}
"
# The comment gate. Both scripts are dependency-free, so they run here rather than only
# inside the installed halves -- which means `make fast` catches a comment too. Running
# them is also the only way to know the rule holds in what we ship: AGENTS.md has banned
# comments since the first commit, and the tree had twelve when the gate was written.
need frontend/devtools/comments.mjs
need backend/devtools/comments.py
# The scanner is subtle enough to have shipped a false positive twice before the table
# existed -- a regex ending in an escaped slash, and a character class holding `/*`,
# which also opened a block comment that ran to the end of the file and hid every real
# comment below it. A gate that cries wolf is a gate people route around, and one that
# goes quiet is worse. The table is what holds both directions.
need frontend/tests/comments.test.ts
need_grep 'startsARegex' frontend/devtools/comments.mjs
need_grep 'devtools/comments.mjs' frontend/package.json
need_grep 'devtools/comments.py' backend/devtools/lint.py
(cd frontend && node devtools/comments.mjs src tests e2e devtools) \
    || fail "the rendered frontend carries a comment"
(cd backend && python3 devtools/comments.py app tests devtools) \
    || fail "the rendered backend carries a comment"

need_grep 'max-complexity' backend/pyproject.toml
need_grep 'max-statements' backend/pyproject.toml
need_grep '\[tool.complexity\]' backend/pyproject.toml
for family in SIM RET PIE C4 PERF ERA C90 PLR0915; do
    grep -q "\"$family\"" backend/pyproject.toml || { echo "ruff family $family missing" >&2; exit 1; }
done

echo "==> assert the supply-chain policy"
need_grep 'minimumReleaseAge: 20160' frontend/pnpm-workspace.yaml
# The dlx cache is held for a month so `make pre-commit` does not reach the registry
# on the first commit of each day. That is only safe while every dlx call names an
# exact version, so assert the one we ship still does.
need_grep 'dlxCacheMaxAge' frontend/pnpm-workspace.yaml
need_grep 'openapi-typescript@7\.13\.0' frontend/package.json
need_grep 'trustPolicy: no-downgrade' frontend/pnpm-workspace.yaml
need_grep 'semver@6.3.1' frontend/pnpm-workspace.yaml
need_grep 'exclude-newer = "14 days"' backend/pyproject.toml
# An exclusion buys back one specific package from one specific policy, and the whole
# value of that is in how narrow it is. A bare name exempts every future release of
# it too, which is a permanent hole opened to close a temporary one.
python3 - <<'PY'
import re

text = open("frontend/pnpm-workspace.yaml", encoding="utf-8").read()
for key in ("trustPolicyExclude", "minimumReleaseAgeExclude"):
    block = re.search(rf"^{key}:\n((?:[ \t]*-[ \t].*\n)+)", text, re.M)
    if block is None:
        continue
    for line in block.group(1).splitlines():
        entry = line.strip().lstrip("-").strip().strip("\"'")
        assert "@" in entry.lstrip("@"), f"{key} entry is not pinned to a version: {entry}"
PY
# A floor alone lets a release published next month decide what a project generated
# next month resolves -- and fastapi and pydantic both write part of openapi.json,
# which is committed and diffed in CI. Runtime dependencies are bounded both ways.
python3 - <<'PY'
import tomllib

deps = tomllib.load(open("backend/pyproject.toml", "rb"))["project"]["dependencies"]
unbounded = [dep for dep in deps if "<" not in dep]
assert not unbounded, f"runtime dependencies with no upper bound: {unbounded}"
PY
# A relative duration needs a uv new enough to parse one. 0.9.7 rejects "14 days"
# with a date-parsing error that names neither uv nor the version, so the floor and
# the version CI installs have to agree, and both have to be at least this.
need_grep 'required-version = ">=0.11.25"' backend/pyproject.toml
need_grep 'version: "0.11.25"' .github/workflows/ci.yml
need_grep 'UV_EXCLUDE_NEWER' Makefile
need_grep 'UV_EXCLUDE_NEWER' .github/workflows/ci.yml
# A tag moves; a commit does not.
if grep -E 'uses: .*@v[0-9]' .github/workflows/ci.yml; then
    echo "the workflow above pins an action by tag rather than by commit" >&2
    exit 1
fi
# pnpm/action-setup reads its version from a package.json at the repository root, and
# this project deliberately has none -- the frontend half owns it. Without pointing
# the action at that file the workflow dies before installing anything, which local
# checks cannot see because they never run the workflow.
need_grep 'package_json_file: frontend/package.json' .github/workflows/ci.yml
# No lockfile ships, and CI installs frozen, so the first push of a generated project
# fails unless the setup instructions say to commit the ones `make install` writes.
need_absent frontend/pnpm-lock.yaml
need_absent backend/uv.lock
need_grep 'pnpm-lock.yaml' docs/installation.md

echo "==> assert hook activation is worktree-safe"
(
    HOOKWORK="$WORK/hooks"
    mkdir -p "$HOOKWORK"
    cd "$HOOKWORK"
    git init -q -b main repo
    cd repo
    git config user.email test@example.com
    git config user.name "Test"
    mkdir -p .githooks devtools
    cp "$OUT/devtools/install-hooks.sh" devtools/
    printf '#!/bin/sh\necho skipping checks\n' >.githooks/pre-commit
    chmod +x .githooks/pre-commit devtools/install-hooks.sh
    git add -A
    git commit -qm initial

    # A hook this repo does not ship must survive, and one it does must chain rather
    # than clobber -- losing someone else's hook is a silent, expensive failure.
    printf '#!/bin/sh\necho FOREIGN-PRE\n' >.git/hooks/pre-commit
    printf '#!/bin/sh\necho FOREIGN-POST\n' >.git/hooks/post-commit
    chmod +x .git/hooks/pre-commit .git/hooks/post-commit

    sh devtools/install-hooks.sh >/dev/null
    need_exec .git/hooks/pre-commit
    need_grep '.githooks/pre-commit' .git/hooks/pre-commit
    need_exec .git/hooks/pre-commit.local
    test -z "$(git config --get core.hooksPath || true)"
    need_grep FOREIGN-POST .git/hooks/post-commit

    echo change >file.txt
    git add file.txt
    out="$(git commit -m second 2>&1)"
    echo "$out" | grep -q FOREIGN-PRE
    echo "$out" | grep -q 'skipping checks'

    # A second foreign hook extends the chain rather than replacing the first.
    printf '#!/bin/sh\necho SECOND-PRE\n' >.git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
    sh devtools/install-hooks.sh >/dev/null
    need_exec .git/hooks/pre-commit.local
    need_exec .git/hooks/pre-commit.local.2

    # Re-arming with nothing new installed must not grow the chain.
    sh devtools/install-hooks.sh >/dev/null
    need_absent .git/hooks/pre-commit.local.3

    # A hooksPath that resolves to the directory just written is equivalent, not a
    # conflict; symlinks and relative paths must compare equal (/var vs /private/var
    # on macOS is the case that bites).
    hooks_abs="$(cd .git/hooks && pwd -P)"
    ln -s "$hooks_abs" ../hooks-link
    for equivalent in "$hooks_abs" ".git/hooks" "../hooks-link"; do
        git config core.hooksPath "$equivalent"
        if ! sh devtools/install-hooks.sh >/dev/null 2>&1; then
            echo "install-hooks.sh rejected an equivalent hooksPath: $equivalent" >&2
            exit 1
        fi
    done
    mkdir -p "$HOOKWORK/elsewhere"
    for diverting in "$HOOKWORK/elsewhere" "$HOOKWORK/does-not-exist"; do
        git config core.hooksPath "$diverting"
        if sh devtools/install-hooks.sh >/dev/null 2>&1; then
            echo "install-hooks.sh accepted a diverting hooksPath: $diverting" >&2
            exit 1
        fi
    done
    git config --unset core.hooksPath
)

echo "==> assert the license variant"
case "$VARIANT" in
    default)
        need LICENSE
        need_grep 'MIT License' LICENSE
        need_grep '"license": "MIT"' frontend/package.json
        need_grep 'license = "MIT"' backend/pyproject.toml
        ;;
    proprietary)
        need LICENSE
        grep -qi 'proprietary' LICENSE
        need_grep '"license": "UNLICENSED"' frontend/package.json
        need_grep 'LicenseRef-Proprietary' backend/pyproject.toml
        ;;
    no-license)
        need_absent LICENSE
        need_grep '"license": "UNLICENSED"' frontend/package.json
        if grep -qE '^license = ' backend/pyproject.toml; then
            echo "backend/pyproject.toml declares a license in the no-license variant" >&2
            exit 1
        fi
        ;;
esac

if [ "$FAST" = "1" ]; then
    echo "==> OK ($VARIANT, fast)"
    exit 0
fi

run() {
    label="$1"
    shift
    if ! "$@" >"$WORK/step.log" 2>&1; then
        echo "--- $label failed ---" >&2
        cat "$WORK/step.log" >&2
        exit 1
    fi
}

echo "==> git init (the documented first step, and the hooks need it)"
git init -q -b main
git config user.email test@example.com
git config user.name "Test"
git add -A
git -c core.hooksPath=/dev/null commit -qm "Initial commit"

echo "==> backend: install, lint, test"
run "uv sync" sh -c 'cd backend && uv sync --all-groups'
run "backend lint" sh -c 'cd backend && uv run python devtools/lint.py --check'
run "pytest" sh -c 'cd backend && uv run pytest -q'

echo "==> frontend: install, lint, test, build"
run "pnpm install" pnpm -C frontend install --prefer-offline

echo "==> assert a fresh install ships no known high-severity vulnerability"
# The cool-off delays a security patch exactly as long as it delays anything else,
# so for a fortnight after a fix the only resolvable version is the vulnerable one.
# No file in this repository changes when that happens and no other check notices:
# the audit runs against what a new project actually resolved, today. The fix is an
# exact minimumReleaseAgeExclude entry naming the patched version, never a wider
# policy. This runs against production dependencies only -- a vulnerable linter is
# not something a user of a generated project ships.
# Only stdout is captured: `audit` exits non-zero on a finding, so the exit status
# cannot be trusted here, and anything it says about why it could not run belongs on
# the console rather than in a file nobody reads.
pnpm -C frontend audit --prod --json >"$WORK/audit.json" || true
python3 - "$WORK/audit.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        report = json.load(handle)
except (OSError, ValueError):
    # A registry that cannot be reached is not a vulnerability, and failing here
    # would teach people that a red run means the network. Say so and move on.
    print("check: pnpm audit returned no report; skipping (registry unreachable?)")
    raise SystemExit(0)

serious = [
    advisory
    for advisory in (report.get("advisories") or {}).values()
    if advisory.get("severity") in ("high", "critical")
]
for advisory in serious:
    print(
        f"check: {advisory['severity']} {advisory['module_name']} "
        f"{advisory.get('vulnerable_versions')} -- {advisory.get('github_advisory_id')}\n"
        f"       patched in {advisory.get('patched_versions')}: {advisory.get('url')}",
        file=sys.stderr,
    )
if serious:
    print(
        "check: pin the patched version in template/frontend/pnpm-workspace.yaml, with\n"
        "       an exact minimumReleaseAgeExclude entry if it has not aged past the\n"
        "       cool-off yet.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

run "frontend lint" pnpm -C frontend lint:check
run "vitest" pnpm -C frontend test
run "vite build" pnpm -C frontend build

echo "==> assert the build output"
need frontend/dist/index.html
ls frontend/dist/assets/index-*.js >/dev/null
ls frontend/dist/assets/index-*.css >/dev/null
# Mock mode is a development and test concern. Shipping the worker to users would
# have it answering their requests.
if grep -rl 'setupWorker' frontend/dist/assets/*.js >/dev/null 2>&1; then
    echo "msw leaked into the production bundle" >&2
    exit 1
fi

echo "==> assert the contract artifacts are in sync with the code"
run "make openapi" make openapi
git diff --exit-code -- openapi.json frontend/src/api/schema.ts \
    || { echo "the committed contract artifacts do not match the rendered code" >&2; exit 1; }

echo "==> the contract suite, against both implementations"
# The whole point of the fullstack template: the halves are checked against each
# other, not only against themselves. Every check above passes on a project whose
# frontend cannot reach its backend at all.
run "contract suite (mocks)" pnpm -C frontend test:contract
run "contract suite (live backend)" make test-contract

echo "==> the store, against a real Postgres"
# The opt-in tier, exercised here when a daemon is available. Gated rather than required,
# because this script is the generator's own gate and must run on a laptop with no Docker --
# and printed either way, because a silently skipped tier is indistinguishable from a passing
# one. The generated project's `make db-test` is the same command; this is the generator
# checking that the command it ships works.
if docker info >/dev/null 2>&1; then
    run "make db-test" make db-test
    echo "    postgres: yes (container) -- store contract and migration suites ran"
else
    echo "    postgres: NO DOCKER -- backend/tests/test_postgres.py and the Postgres half of"
    echo "    the store contract did not run. Everything about the schema that a fake"
    echo "    connection can answer did (backend/tests/test_schema.py), and nothing here"
    echo "    proves the DDL parses."
fi

echo "==> the dev loop starts both halves, and Ctrl-C stops both halves"
# `make dev` is the command every user of a generated project runs first and runs
# most, and the way it breaks leaves no trace in a diff: anything that replaces the
# shell -- an `exec`, a `cd &&` chain ending in one -- discards the cleanup trap, so
# stopping the frontend leaves uvicorn holding :8000. The next `make dev` dies on a
# port nothing visible is using, in a different session, and the cause is thirty
# lines up a file nobody is looking at. The greps above catch that one edit coming
# back; this starts it for real and stops it the way a person does.
#
# It runs under a pty because nothing else is faithful. dev.sh keeps the frontend in
# the foreground so vite owns the terminal, which means a signal sent to the script
# alone cannot be handled until vite returns -- a plain `kill` would deadlock here
# and prove nothing. Writing 0x03 to the pty master makes the line discipline raise
# SIGINT on the foreground process group, which is what the key does.
python3 - "$OUT" <<'PY'
import os
import pty
import signal
import subprocess
import sys
import time

BACKEND = "http://localhost:8000/tasks"
PROXY = "http://localhost:5173/api/tasks"


def serving(url):
    return subprocess.run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


# Before starting anything: a port already in use makes every assertion below lie.
# dev.sh would fail to bind, the readiness probe would be answered by whatever is
# already there, and the check would report that dev.sh stranded a server -- naming
# the one bug it is here to catch, on a run where it did nothing wrong.
already = " and ".join(n for n, u in ((":8000", BACKEND), (":5173", PROXY)) if serving(u))
if already:
    print(f"check: {already} already in use before dev.sh started -- something else is\n"
          f"       serving it (another `make dev`?). Stop it and run this again.",
          file=sys.stderr)
    raise SystemExit(1)

pid, master = pty.fork()
if pid == 0:
    os.chdir(sys.argv[1])
    os.execvp("sh", ["sh", "devtools/dev.sh"])

os.set_blocking(master, False)
output = bytearray()


def drain():
    # The child writes into a pty buffer of fixed size, so a reader that stops
    # reading wedges the very process it is testing once that buffer fills.
    while True:
        try:
            chunk = os.read(master, 65536)
        except (BlockingIOError, OSError):
            return
        if not chunk:
            return
        output.extend(chunk)


def until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        drain()
        if predicate():
            return True
        time.sleep(0.5)
    return False


def die(message):
    drain()
    print(f"check: {message}", file=sys.stderr)
    print("--- dev.sh output ---", file=sys.stderr)
    sys.stderr.write(bytes(output).decode("utf-8", "replace"))
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        pass
    raise SystemExit(1)


# Through :5173/api, so this waits on both halves and the proxy between them rather
# than on the backend alone.
if not until(lambda: serving(PROXY), 120):
    die("dev.sh did not serve :5173/api within 120s")

os.write(master, b"\x03")

if not until(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid, 30):
    die("dev.sh was still running 30s after Ctrl-C")

if not until(lambda: not serving(BACKEND) and not serving(PROXY), 15):
    held = " and ".join(n for n, u in ((":8000", BACKEND), (":5173", PROXY)) if serving(u))
    die(f"dev.sh exited but {held} still answering")
PY

echo "==> OK ($VARIANT)"
