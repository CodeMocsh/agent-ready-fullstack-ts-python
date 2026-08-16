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
for target in install hooks lint lint-check test test-fast test-contract dev \
              dev-frontend dev-backend openapi openapi-check build upgrade clean; do
    grep -q "^$target:" Makefile || { echo "Makefile is missing the $target target" >&2; exit 1; }
done
# CI builds and tests; it does not deploy, and it does not publish.
test "$(ls .github/workflows)" = "ci.yml"

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
need backend/app/store.py
need backend/tests/test_tasks.py
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
# The contract suite has no CI job by design, so the hook is its only gate. A hook
# that lints but never runs it would leave the check with nowhere to fire.
need_grep 'contract-test.sh' .githooks/pre-commit

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
for (const key of ['complexity', 'conformance']) {
  if (!pkg[key].exclude.includes('src/api/schema.ts')) {
    throw new Error(key + '.exclude is missing src/api/schema.ts');
  }
}
const biome = require('./frontend/biome.json');
if (!biome.files.includes.includes('!src/api/schema.ts')) {
  throw new Error('biome.json does not exclude src/api/schema.ts');
}
"
need_grep 'max-complexity' backend/pyproject.toml
need_grep 'max-statements' backend/pyproject.toml
need_grep '\[tool.complexity\]' backend/pyproject.toml
for family in SIM RET PIE C4 PERF ERA C90 PLR0915; do
    grep -q "\"$family\"" backend/pyproject.toml || { echo "ruff family $family missing" >&2; exit 1; }
done

echo "==> assert the supply-chain policy"
need_grep 'minimumReleaseAge: 20160' frontend/pnpm-workspace.yaml
need_grep 'trustPolicy: no-downgrade' frontend/pnpm-workspace.yaml
need_grep 'semver@6.3.1' frontend/pnpm-workspace.yaml
need_grep 'exclude-newer = "14 days"' backend/pyproject.toml
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

echo "==> OK ($VARIANT)"
