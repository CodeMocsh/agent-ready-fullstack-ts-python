#!/bin/sh
# Exercise a rendered template. One script, two callers: the pre-commit hook, and
# your laptop. When these steps lived only in the workflow file they could only run
# on GitHub, and a check that cannot run locally is a check people learn to discover
# late.
#
# Rendering itself is devtools/render.sh, which prints a project and is worth running
# on its own while working on any one check here.
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
export UV_EXCLUDE_NEWER="14 days"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RENDER="$REPO/devtools/render.sh"
COPIER_SPEC="$(sh "$RENDER" --spec)"

# `set -e` makes a bare `test -f` exit silently, which turns a one-line problem into
# a bisect. These say what they were looking for.
fail() { echo "check: $*" >&2; exit 1; }
need() { [ -f "$1" ] || fail "expected file: $1"; }
need_exec() { [ -x "$1" ] || fail "expected executable file: $1"; }
need_absent() { [ ! -e "$1" ] || fail "expected no such file: $1"; }
need_grep() { grep -q "$1" "$2" || fail "expected /$1/ in $2"; }
need_no_grep() { ! grep -q "$1" "$2" || fail "unexpected /$1/ in $2"; }


# AGENTS.md tells an agent to keep the principle sections word-for-word in step with
# template/AGENTS.md.jinja, and the same file argues that a rule nothing enforces does not
# hold. This is that enforcement. The range ends at the first heading each file owns, so
# everything below the shared block is free to differ. `sed -E` is load-bearing: BSD sed has
# no \| alternation in a basic regex, so the range would never find its end and the check
# would compare whole files, reporting every local section as drift.
shared_prose() {
    sed -E -n "/^## Approach/,/^## (Simplified technical English|Layout|The template is inert)/p" "$1" |
        sed '$d'
}
PROSE_ROOT="$(mktemp)"
PROSE_TEMPLATE="$(mktemp)"
shared_prose "$REPO/AGENTS.md" >"$PROSE_ROOT"
shared_prose "$REPO/template/AGENTS.md.jinja" >"$PROSE_TEMPLATE"
if ! diff -q "$PROSE_ROOT" "$PROSE_TEMPLATE" >/dev/null; then
    echo "check: the principle sections have drifted between the two AGENTS.md files:" >&2
    diff "$PROSE_ROOT" "$PROSE_TEMPLATE" | sed 's/^/check:   /' >&2
    rm -f "$PROSE_ROOT" "$PROSE_TEMPLATE"
    fail "keep Approach, Fail loudly, Zero comments and Simplified technical English identical."
fi
# An empty range means the headings moved and the check silently compared nothing.
[ -s "$PROSE_ROOT" ] || fail "the shared prose range in check_template.sh matched no lines"
rm -f "$PROSE_ROOT" "$PROSE_TEMPLATE"

# One pin, many copies. devtools/render.sh owns it -- it is the script that runs copier
# -- and the value read back through `--spec` above is also written wherever a doc shows
# the command rather than refers to it. The grep below is the inventory, because a doc
# rewrite moves them around. Nothing kept them in step, and the stale copy is the one a user
# pastes: they would generate with a release this script never exercised, and after this
# bump, with one missing two published security fixes. A grep is cheap, so grep.
STALE="$(grep -rEoh 'copier@[0-9]+\.[0-9]+\.[0-9]+' "$REPO" \
    --include='*.md' --include='*.jinja' --include='*.sh' --include='*.yml' \
    2>/dev/null | sort -u | grep -v "^$COPIER_SPEC\$" || true)"
if [ -n "$STALE" ]; then
    echo "check: this repo pins $COPIER_SPEC, but also names:" >&2
    echo "$STALE" | sed 's/^/check:   /' >&2
    echo "check: every copier@ in the repo is a command someone runs. Bump them together." >&2
    exit 1
fi

# What this script needs. uvx and tar are render.sh's and are probed there, so they are
# not repeated here; git is in both lists because both use it -- render.sh to commit the
# staged template, this script to init the smoke project and diff the contract artifacts.
for tool in uv git python3 node pnpm curl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "check: $tool is required and not on PATH." >&2
        echo "check: see template/docs/installation.md for how to install it." >&2
        exit 1
    fi
done

# This script starts servers twice -- the contract suite's pair, and the dev loop's --
# and both take their ports from the environment exactly as a generated project does.
# Set them here, once, for the whole run: setting them any later leaves every step above
# that line on :8000 and :5173, which is the pair a `make dev` in some other checkout is
# most likely to be holding, and a run that dies there fails on somebody else's process
# rather than on anything it is testing.
#
# The defaults are used when they are free, so the ordinary run exercises the ports a
# user actually gets; when they are not, a free pair is allocated rather than the run
# refusing to start. Either way it is printed, because a check that quietly moves is a
# check whose logs cannot be read six months later.
#
# A named port is obeyed rather than treated as a preference: someone who sets one has
# something pointed at it, and answering "I used a different one" three hundred lines
# later is worse than refusing here with the reason. The two are decided independently,
# so naming one does not stop the other from moving out of the way.
#
# This stays here rather than moving into render.sh with the rest of the setup, and the
# reason is the `held` list below: each allocation keeps its socket bound until the
# process that made it exits, which is what stops two of them coming back as the same
# number. Allocating in render.sh would release every one of them the moment it
# returned, handing this script a pair that was free a second ago and telling it they
# still are. Rendering does not need a port anyway.
PORTS="$(python3 - "${BACKEND_PORT:+named}" "${BACKEND_PORT:-8000}" \
                   "${FRONTEND_PORT:+named}" "${FRONTEND_PORT:-5173}" <<'PY'
import socket
import sys

LOOPBACKS = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))


def free(port):
    # Both families, because uvicorn and vite are told to serve "localhost" and that is
    # two addresses. A probe on one of them alone calls a port free that the other half
    # cannot have -- which is exactly how the vite of another checkout, holding [::1]
    # only, goes unnoticed until --strictPort refuses in the middle of the run.
    for family, host in LOOPBACKS:
        try:
            probe = socket.socket(family)
        except OSError:
            continue
        with probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


NAMES = ("BACKEND_PORT", "FRONTEND_PORT")
requested = [(bool(sys.argv[1]), int(sys.argv[2])), (bool(sys.argv[3]), int(sys.argv[4]))]
if requested[0][1] == requested[1][1]:
    raise SystemExit(f"check: {NAMES[0]} and {NAMES[1]} are both :{requested[0][1]}.")

# Each allocation stays bound until this process exits, so no two of them can come back
# as the same number.
held = []
chosen = []
for name, (named, port) in zip(NAMES, requested):
    if free(port):
        chosen.append(port)
        continue
    if named:
        raise SystemExit(
            f"check: :{port} is in use, and {name} asked for it.\n"
            "check: something else is serving it (a `make dev` in another checkout?).\n"
            f"check: stop it, name a free port, or unset {name} and let this script pick."
        )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    held.append(sock)
    chosen.append(sock.getsockname()[1])
print(" ".join(str(port) for port in chosen))
PY
)"
BACKEND_PORT="${PORTS%% *}"
FRONTEND_PORT="${PORTS##* }"
export BACKEND_PORT FRONTEND_PORT

WORK="$(mktemp -d)"

# One handler for the whole run, because `set -e` is live inside a trap: a `kill` of a
# process that has already gone returns non-zero, and an inline handler would skip the
# `rm -rf` after it and leak a temp tree holding a node_modules and a venv. Steps that
# start something long-lived set `serve_pid` and leave the stopping to this.
cleanup() {
    if [ -n "${serve_pid:-}" ]; then
        kill "$serve_pid" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

echo "==> render ($VARIANT)"
echo "    ports: backend :$BACKEND_PORT, frontend :$FRONTEND_PORT"
OUT="$(sh "$RENDER" "$VARIANT" --into "$WORK/render")"

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

echo "==> assert every document is named, and names only documents that exist"
python3 "$REPO/devtools/links_test.py" \
    || fail "links.py stopped recognising a citation, so the two sweeps below would prove nothing."
# The three orphans are found by filename rather than by a link: GitHub reads the pull
# request template, and an agent reads CLAUDE.md, without either being named anywhere.
python3 "$REPO/devtools/links.py" "$REPO" --exclude template --exclude devtools/links_test.py \
    --allow-orphan README.md --allow-orphan CLAUDE.md \
    --allow-orphan .github/PULL_REQUEST_TEMPLATE.md \
    || fail "a document names something that does not exist, or nothing names it."
python3 "$REPO/devtools/links.py" "$OUT" \
    --allow-orphan README.md --allow-orphan CLAUDE.md \
    || fail "the generated project names a document that does not exist, or orphans one."

echo "==> assert every workflow is valid, here and in what ships"
# A workflow cannot report its own breakage. A malformed check.yml does not fail the
# check -- it fails to start, and the pull request shows nothing where the gate should
# be, which reads as a repository that has no gate rather than one whose gate is broken.
# The same is true of the workflow every generated project inherits, and that one breaks
# in somebody else's repository.
#
# Pinned to a version and a hash rather than tracking latest: a linter that changes under
# you turns an unrelated push red, and this download is the one step in the gate that
# nobody would think to audit.
ACTIONLINT_VERSION=1.7.12
case "$(uname -s)/$(uname -m)" in
    Darwin/arm64)
        ACTIONLINT_PLATFORM=darwin_arm64
        ACTIONLINT_SHA=aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
    Darwin/x86_64)
        ACTIONLINT_PLATFORM=darwin_amd64
        ACTIONLINT_SHA=5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
    Linux/aarch64 | Linux/arm64)
        ACTIONLINT_PLATFORM=linux_arm64
        ACTIONLINT_SHA=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
    Linux/x86_64)
        ACTIONLINT_PLATFORM=linux_amd64
        ACTIONLINT_SHA=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
    *)
        fail "no actionlint pin for $(uname -s)/$(uname -m). Add one rather than skipping the workflow lint: a platform that cannot check its workflows is a platform that pushes them unchecked." ;;
esac

# macOS ships shasum and no sha256sum; most Linux images ship the reverse. A verification
# step that quietly does nothing when neither is present is worse than no verification.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        fail "neither sha256sum nor shasum is on PATH, so the actionlint download cannot be verified."
    fi
}

ACTIONLINT_DIR="$WORK/actionlint"
mkdir -p "$ACTIONLINT_DIR"
curl -fsSL -o "$ACTIONLINT_DIR/actionlint.tar.gz" \
    "https://github.com/rhysd/actionlint/releases/download/v$ACTIONLINT_VERSION/actionlint_${ACTIONLINT_VERSION}_${ACTIONLINT_PLATFORM}.tar.gz" \
    || fail "could not download actionlint $ACTIONLINT_VERSION for $ACTIONLINT_PLATFORM."
ACTIONLINT_GOT="$(sha256_of "$ACTIONLINT_DIR/actionlint.tar.gz")"
[ "$ACTIONLINT_GOT" = "$ACTIONLINT_SHA" ] \
    || fail "actionlint $ACTIONLINT_VERSION $ACTIONLINT_PLATFORM hashed $ACTIONLINT_GOT, and the pin says $ACTIONLINT_SHA."
tar -xzf "$ACTIONLINT_DIR/actionlint.tar.gz" -C "$ACTIONLINT_DIR" actionlint \
    || fail "the actionlint archive carried no actionlint binary."

# Both trees. A glob that matches nothing expands to itself, and actionlint given a path
# that is not there exits clean -- which is how this check would come to lint neither
# tree while still printing. So the directory and the file list are asserted first, and
# `-exec +` hands the files over without a word-splitting expansion.
for workflows in "$REPO/.github/workflows" "$OUT/.github/workflows"; do
    [ -d "$workflows" ] || fail "expected workflows in $workflows, and the directory is absent."
    find "$workflows" \( -name '*.yml' -o -name '*.yaml' \) -print >"$WORK/workflow-list"
    [ -s "$WORK/workflow-list" ] || fail "$workflows holds no workflow, so this check checked nothing."
    xargs "$ACTIONLINT_DIR/actionlint" <"$WORK/workflow-list" \
        || fail "a workflow in $workflows is invalid, and a broken workflow cannot report its own breakage."
    # Pinned to a commit, never to a tag. A tag moves, so a pull request that trusts one
    # runs whatever that tag pointed at this morning -- and the token it runs under can
    # read this repository.
    while IFS= read -r workflow; do
        unpinned="$(grep -nE '^[[:space:]]*-?[[:space:]]*uses:' "$workflow" | grep -vE '@[0-9a-f]{40}' || true)"
        [ -z "$unpinned" ] || fail "$workflow uses an action that is not pinned to a commit: $unpinned"
    done <"$WORK/workflow-list"
done

# The generated project's backend/tests/test_gate.py refuses a workflow that re-lists the
# gate's steps instead of naming the target. This repo asserts the same of its own, because
# a rule the template asserts and its own generator ignores is a rule nobody believes.
#
# Comments are stripped first. Every reason this file gives for naming the target is
# written in a comment inside the workflow, so a grep that reads them passes on a workflow
# whose steps say something else entirely.
uncommented() { sed 's/#.*//' "$1"; }
uncommented "$REPO/.github/workflows/check.yml" | grep -q 'make check' \
    || fail "check.yml no longer runs 'make check' outside a comment. It names the target the hook runs; it does not re-spell the steps."
if uncommented "$REPO/.github/workflows/check.yml" | grep -q 'check_template.sh'; then
    fail "check.yml calls check_template.sh directly. Name the make target, so the workflow and the hook cannot run different things."
fi
uncommented "$REPO/.github/workflows/release.yml" | grep -q 'VERSION' \
    || fail "release.yml no longer reads VERSION, so nothing in the repository decides which version it would cut."

# A workflow that lints clean can still be wired wrong, and the wiring is what carries
# the rules. no-mistakes keeps a test per workflow for this reason; these are the
# invariants of ours that nothing else would notice going.
#
# The release trigger first. It ran on `push: main` once, which raced the gate and could
# publish a tag from a tree that had not passed -- the defect is invisible in a diff that
# only changes two lines of `on:`, and its blast radius is a version users generate from.
uncommented "$REPO/.github/workflows/release.yml" | grep -q 'workflow_run:' \
    || fail "release.yml no longer waits on a workflow_run. It must run after the gate, never beside it: a tag cut from an unchecked tree is what this trigger exists to prevent."
if uncommented "$REPO/.github/workflows/release.yml" | grep -qE '^[[:space:]]*push:'; then
    fail "release.yml triggers on a push again, which races the gate and can publish before it passes."
fi
uncommented "$REPO/.github/workflows/release.yml" | grep -q "conclusion == 'success'" \
    || fail "release.yml no longer refuses a failed gate, so a red check would still cut a tag."

# Nothing here may merge or approve. That is a decision about who is in charge of this
# repository rather than a detail, and today it holds only because nobody has added the
# permission -- which is not the same as it being refused.
for workflow in "$REPO"/.github/workflows/*.yml; do
    if uncommented "$workflow" | grep -qE 'pull-requests:[[:space:]]*write|gh pr merge|--auto\b'; then
        fail "$workflow can act on a pull request. No workflow here merges or approves: the gate says a change may land, and a person decides that it does."
    fi
done

# VERSION is the whole of what the release workflow reads, and it reads it after a merge,
# where a refusal is a release that silently did not ship. So the value is proved here, on
# every pull request, while it is still something a person can fix.
echo "==> assert VERSION names a version a release could be cut from"
need "$REPO/VERSION"
CLAIMED="$(tr -d ' \t\n\r' <"$REPO/VERSION")"
case "$CLAIMED" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) fail "VERSION reads '$CLAIMED', which is not a semantic version, so release.yml would refuse after the merge." ;;
esac
# A tag is permanent, so a version already released must never be claimed by a commit that
# would move it. release.yml refuses that after the merge; this says so before the push.
if git -C "$REPO" rev-parse -q --verify "refs/tags/v$CLAIMED^{commit}" >/dev/null 2>&1; then
    if [ "$(git -C "$REPO" rev-parse "refs/tags/v$CLAIMED^{commit}")" != "$(git -C "$REPO" rev-parse HEAD)" ]; then
        fail "VERSION claims $CLAIMED, and v$CLAIMED already tags a different commit. A released version is never moved: claim a new one."
    fi
fi

if [ "$VARIANT" = "default" ]; then
    echo "==> assert hostile answers stay data, not syntax"
    # The free-form questions are prose, and some of the files their answers land
    # in are hand-written JSON and TOML. An answer interpolated raw takes its quotes
    # and backslashes with it, and the result is a manifest no tool can parse -- a
    # failure that surfaces in someone else's project, on `pnpm install`, with
    # nothing in the diff to suggest why. This renders what a person could plausibly
    # type and parses both manifests back. It runs once: nothing here varies with
    # the license.
    H_DESC='A "quoted" project: Bob'"'"'s tasks & <friends>, C:\notes'
    H_AUTHOR='O'"'"'Brien "Bob" \ Tester'
    H_EMAIL="o'brien@example.com"
    H_ORG='test"org'
    export H_DESC H_AUTHOR H_EMAIL H_ORG
    HOSTILE="$(sh "$RENDER" --into "$WORK/hostile" -- \
        --data package_description="$H_DESC" \
        --data package_author_name="$H_AUTHOR" \
        --data package_author_email="$H_EMAIL" \
        --data package_github_org="$H_ORG")"
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
# The gate is one named list so the hook and a laptop cannot drift apart, and the
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
# No test decides for itself whether to run. Everything that needs a server is a *tier* --
# backend/tests/integration/, held out of the default pytest run by norecursedirs and run
# whole by `make db-test` -- because a test that skips itself exits 0 and reads exactly like
# a test that passed, and a run where every one of them skipped looks green. The generated
# project polices this from then on in test_gate.py; this is what makes it ship that way.
need_grep '^norecursedirs = \["integration"\]' backend/pyproject.toml
need_grep '^DB_TEST_SUITE = tests/integration$' Makefile
need backend/tests/tiers.py
# The tier is out of the default run, so the default run has to say so. Without this line a
# green `make test` reads as "everything passed" on a project where a whole folder was never
# looked at -- the same silence a skip produces, one level up.
need backend/tests/conftest.py
need_grep 'not in this run' backend/tests/conftest.py
# The scan itself comes from the generated project's own test_gate.py rather than being
# written out again here. A second copy in shell syntax is a copy that drifts, and the half
# that drifts is the half nobody notices: this runs in `make fast`, where no test does.
# test_gate.py imports no third-party package and defers its one version-dependent import,
# so the host python3 can read it whatever the project's own interpreter is.
python3 - <<'PY' || fail "the skip scan did not pass; see above"
import sys

sys.path.insert(0, "backend")
try:
    from tests.test_gate import ROOT, switched_off
except ImportError as error:
    print(f"check: the skip scan could not run, which is not the same as passing: {error}",
          file=sys.stderr)
    sys.exit(1)

found = switched_off()
for path, marker in found:
    print(f"{path.relative_to(ROOT)} uses {marker!r}, and a skipped test exits 0 like a "
          f"passing one -- move it into a tier instead", file=sys.stderr)
sys.exit(1 if found else 0)
PY
# The suite is sorted tier first, then source. A folder per area is what keeps a growing
# project from piling every file at the top of tests/, and it is what the tier targets
# select on. test_gate.py stays at the top because it covers no source: it reads this
# Makefile, the hook and the workflow.
for folder in devtools identity integration routes serve store; do
    need "backend/tests/$folder/__init__.py"
done
# The db-test recipe traps on INT so an interrupted suite does not leak its container, and
# `trap ... EXIT INT TERM` only fires on Ctrl-C under bash -- dash runs the handler after the
# foreground command returns, which on Ctrl-C it never does.
need_grep '^SHELL := /bin/bash' Makefile
# A workflow ships, and it runs `make pre-commit` -- the target the git hook runs, so the
# two cannot drift. The hook checks the machine that commits; the workflow checks a fresh
# checkout, which is what covers a clone where `make hooks` was never run. Asserted here as
# well as in test_gate.py because that test walks the workflows it finds, and a directory
# that stopped existing is a walk over nothing.
need .github/workflows/ci.yml
need_grep 'make pre-commit' .github/workflows/ci.yml
need_no_grep '\.jinja' .github/workflows/ci.yml

# test_gate.py decides what runs by reading files as text, and a text check can be
# satisfied by a string that means nothing: `"e2e" in playwright.config.ts` was true of a
# config pointing at a directory holding none of the tier's specs, so the assertion passed
# while `make test-e2e` ran nothing. That shipped, and the downstream project found it.
#
# So each of these is asserted in the failing direction, which is the only direction that
# distinguishes a working check from a green one. Break what the check exists to catch,
# require it to fail, put the file back. The passing direction is the line above it.
echo "==> assert the gate's own checks fail on what they are for"
python3 - <<'PY' || fail "a check in test_gate.py did not refuse what it is written to refuse"
import pathlib
import shutil
import sys

sys.path.insert(0, "backend")
try:
    from tests import test_gate
except ImportError as error:
    print(f"check: test_gate.py could not be imported, which is not the same as passing: {error}",
          file=sys.stderr)
    sys.exit(1)

failures = []


def refused(name):
    try:
        getattr(test_gate, name)()
    except AssertionError:
        return True
    return False


def mutated(name, originals, before, after, describes, where):
    if not any(before in text for text in originals.values()):
        failures.append(f"nothing in {where} says {before!r}, so this mutation tests nothing")
        return
    try:
        for file, text in originals.items():
            file.write_text(text.replace(before, after), encoding="utf-8")
        if not refused(name):
            failures.append(f"{name} passed {describes}")
    finally:
        for file, text in originals.items():
            file.write_text(text, encoding="utf-8")


def mutation(name, path, before, after, describes):
    file = pathlib.Path(path)
    mutated(name, {file: file.read_text(encoding="utf-8")}, before, after, describes, path)


def emptied_tier(name, root, holds, declares, describes):
    """A tier stops holding tests file by file, and the check passes while one is left, so
    every file in the folder has to lose its declaration at once."""
    files = sorted(pathlib.Path(root).rglob(holds))
    if not files:
        failures.append(f"no {holds} under {root}, so this mutation tests nothing")
        return
    originals = {file: file.read_text(encoding="utf-8") for file in files}
    mutated(name, originals, declares, declares.replace("test", "check"), describes, root)


for name in ("test_every_tier_is_selected_by_a_file_that_still_names_it",
             "test_every_tier_still_holds_tests",
             "test_a_workflow_runs_the_gate_rather_than_a_copy_of_it"):
    if refused(name):
        failures.append(f"{name} fails on the tree as rendered, before any mutation")

mutation("test_every_tier_is_selected_by_a_file_that_still_names_it",
         "frontend/playwright.config.ts", 'testDir: "./e2e"', 'testDir: "./specs-e2e"',
         "a Playwright config pointed at a directory holding none of the tier's specs")

mutation("test_every_tier_is_selected_by_a_file_that_still_names_it",
         "Makefile", "DB_TEST_SUITE = tests/integration", "DB_TEST_SUITE = tests/db",
         "a Makefile that selects a folder the tier does not live in")

# Both spellings of `declares`, because a tier is emptied by its own runner's idea of what a
# test looks like: `def test_` for pytest, `test(` for Playwright. A file that still exists
# and declares nothing is the case a glob cannot see -- the folder is there, the runner
# collects none of it, and the tier is gone while the target still exits 0.
emptied_tier("test_every_tier_still_holds_tests", "backend/tests/integration", "test_*.py",
             "def test_", "a Python tier whose files declare no test pytest would collect")

emptied_tier("test_every_tier_still_holds_tests", "frontend/e2e", "*.spec.ts",
             "test(", "an e2e tier whose specs declare no test Playwright would collect")

planted = pathlib.Path(".github/workflows/planted.yml")
had_github = pathlib.Path(".github").exists()
planted.parent.mkdir(parents=True, exist_ok=True)
planted.write_text("jobs:\n  gate:\n    steps:\n      - run: sh devtools/check_template.sh\n",
                   encoding="utf-8")
try:
    if not refused("test_a_workflow_runs_the_gate_rather_than_a_copy_of_it"):
        failures.append("test_a_workflow_runs_the_gate_rather_than_a_copy_of_it passed a "
                        "workflow that runs check_template.sh, which is a script in the "
                        "generator repository and not a file a generated project holds")
finally:
    planted.unlink()
    if not had_github:
        shutil.rmtree(".github")

for problem in failures:
    print(f"check: {problem}", file=sys.stderr)
sys.exit(1 if failures else 0)
PY

# An exclude or a theme file naming a path that is not there is the quiet half of the same
# failure: the entry matches nothing today and exempts whatever lands there tomorrow. The
# backend's is worse still -- `rglob` on a missing directory answers an empty list, so a
# renamed source folder made the comment gate report clean over nothing at all.
if (cd frontend && node devtools/comments.mjs src --exclude src/nowhere) >/dev/null 2>&1; then
    fail "comments.mjs accepted an exclude naming a path that is not in the tree"
fi
if (cd frontend && node devtools/conformance.mjs src --theme-file src/nowhere.css) >/dev/null 2>&1
then
    fail "conformance.mjs accepted a theme file that is not in the tree"
fi
if (cd backend && python3 devtools/comments.py app nowhere) >/dev/null 2>&1; then
    fail "comments.py accepted a root that is not in the tree, where rglob reports clean"
fi
mkdir -p "$WORK/untokenizable"
printf 'def f():\n    return (\n' >"$WORK/untokenizable/broken.py"
if (cd backend && python3 devtools/comments.py "$WORK/untokenizable") >/dev/null 2>&1; then
    fail "comments.py reported a file it cannot tokenize as carrying no comment"
fi
rm -rf "$WORK/untokenizable"

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
# The same rule one tier out, for the two Playwright configs. Neither is in the gate --
# they need a browser binary this script has no business downloading -- so a hard-coded
# port here fails nowhere and is caught by nothing. The live config is the sharper of
# the two: it starts no server, so a wrong port does not refuse, it silently points at
# whatever else is listening, which in a second checkout is the other checkout's app.
need_grep 'process.env.PREVIEW_PORT' frontend/playwright.config.ts
need_grep 'process.env.FRONTEND_PORT' frontend/playwright.live.config.ts
need_no_grep 'localhost:5173' frontend/playwright.live.config.ts
# Reuse is the other half of the same bug and it is worse, because it produces a green
# run rather than a failure: Playwright checks that something answers on the URL, never
# that the something is this build. A stranger's server on the port absorbs the suite.
need_grep 'reuseExistingServer: false' frontend/playwright.config.ts
# The live spec must name no seed row at all. Those exist only because the in-memory
# substrate put them there -- docs/adr/0001 says Postgres starts empty on purpose -- so an
# assertion on one passes against the default `make dev` and fails against a real database,
# on a fixture nothing in this half declares. It creates what it asserts instead.
#
# Read against the seed itself rather than against one title spelled here. Naming a single
# row would leave the other two enforceable in name only: the next spec to reach for "Run
# the app in mock mode" would pass this gate and fail on Postgres, which is the whole of
# what the rule exists to stop.
python3 - <<'PY'
import ast
import pathlib
import sys

memory = pathlib.Path("backend/app/store/memory.py")
tree = ast.parse(memory.read_text(encoding="utf-8"))
seeds = [
    ast.literal_eval(node.value)
    for node in ast.walk(tree)
    if isinstance(node, (ast.Assign, ast.AnnAssign))
    and node.value is not None
    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    if getattr(target, "id", "") == "SEED"
]
if not seeds:
    print(f"check: no SEED found in {memory}", file=sys.stderr)
    raise SystemExit(1)

titles = [row[1] for row in seeds[0]]
live = pathlib.Path("frontend/e2e/tasks.live.spec.ts")
named = [title for title in titles if title in live.read_text(encoding="utf-8")]
if named:
    print(f"check: {live} names a seed row: {named}", file=sys.stderr)
    print("check: those rows exist only on the in-memory substrate. Postgres starts",
          file=sys.stderr)
    print("check: empty on purpose (docs/adr/0001), so the spec must create what it",
          file=sys.stderr)
    print("check: asserts rather than assume a fixture this half never declares.",
          file=sys.stderr)
    raise SystemExit(1)
PY
# The mock spec may name one: in mock mode the rows are the frontend's own, declared in
# src/mocks/store.ts, so the assertion and the fixture are in the same half.
need_grep 'Read AGENTS.md' frontend/e2e/tasks.spec.ts
# The Makefile must not name the contract URL. It never exported the variable, so the
# default reached nothing, and re-adding one that did would override the port the script
# actually started the dev server on.
need_no_grep 'CONTRACT_BASE_URL ?=' Makefile

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
# The worker is a public/ asset, so no mode keeps it out of a build; the plugin that deletes
# it again is the only thing standing between a production deploy and a service worker on the
# user's origin. Named here as well as asserted against a real build, so `make fast` notices.
need_grep 'strip-mock-worker' frontend/vite.config.ts
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
need backend/tests/routes/test_tasks.py
# The one-origin entrypoint, for a deployment with no proxy to strip the /api prefix.
# It mounts app.main and delegates that app's lifespan, because Starlette does not run a
# mounted application's lifespan and the process would come up with no database on it.
need backend/app/serve.py
need backend/tests/serve/test_serve.py
# Two substrates behind one contract, and one suite over both. A store package with only
# one implementation in it is a shape nothing checks -- see docs/adr/0001.
need backend/app/store/__init__.py
need backend/app/store/memory.py
need backend/app/store/pg.py
need backend/app/store/ddl.py
need backend/app/store/migrate.py
need backend/app/store/conn.py
# One contract file, and a suite per substrate that runs all of it. Losing either runner
# leaves a green project in which one implementation is checked and the other is claimed.
need backend/tests/store_contract.py
need backend/tests/store/test_store_contract.py
need backend/tests/integration/test_store_contract.py
need backend/tests/store/test_schema.py
need backend/tests/integration/test_postgres.py
need backend/tests/integration/test_isolation.py
need backend/tests/integration/conftest.py
need_absent backend/app/store.py
# The generated schema, committed the way openapi.json is. backend/tests/store/test_schema.py
# fails when it drifts, and that test is in the fast tier, so the gate catches it.
need deploy/schema.sql
need deploy/roles.sql
need deploy/compose.yaml
need_exec deploy/credentials.sh
need CONTEXT.md
# Every decision that ships, named in full. links.py proves each is reachable and that no
# citation dangles, which a record deleted together with everything naming it still passes.
# This is the half that notices such a record left.
for adr in 0001-two-substrates-behind-one-contract \
           0002-tenant-isolation-is-forced-and-always-on \
           0003-the-application-never-applies-ddl \
           0005-a-test-never-decides-whether-to-run \
           0006-the-one-origin-entrypoint-is-the-edge \
           0007-the-spec-describes-what-the-service-actually-does \
           0008-a-route-cannot-escape-the-identity-seam; do
    need "docs/adr/$adr.md"
done

echo "==> assert tenant isolation is wired, not just described"
# FORCE, not ENABLE. A table's owner bypasses its own policies by default, so with ENABLE
# alone the role that applied the schema reads every row -- silently, with nothing anywhere
# reporting a problem. backend/tests/integration/test_isolation.py proves it against a real
# server; this catches the edit before anyone has a database to run that against.
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
# The behavioural guard is backend/tests/integration/test_postgres.py, which fails with `public` if
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
need frontend/tests/api/contract.test.ts
need_grep 'CONTRACT_TARGET' frontend/tests/api/contract.test.ts
# Starting the mock worker during the live run would intercept the very requests
# that run exists to make, and the suite would pass while proving nothing.
need_grep 'CONTRACT_TARGET' frontend/tests/setup.ts
# The contract suite is the only check that can fail on the two halves not
# interoperating, and it reaches the hook through the gate rather than by name.
need_grep 'make -s pre-commit' .githooks/pre-commit
need_grep 'test-contract' Makefile

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
need frontend/tests/devtools/comments.test.ts
# One scanner, not three. The regex handling below used to live in comments.mjs alone,
# and conformance.mjs carried a copy of everything around it with that part missing --
# so a file whose regex held `/*` went quiet in the gate that had no table. Both read
# devtools/scan.mjs now, and each has a table of its own.
need frontend/devtools/scan.mjs
need frontend/devtools/gate.mjs
need frontend/tests/devtools/conformance.test.ts
need_grep 'startsARegex' frontend/devtools/scan.mjs
need_no_grep 'const REGIONS' frontend/devtools/comments.mjs
need_no_grep 'const REGIONS' frontend/devtools/conformance.mjs
need_grep 'devtools/comments.mjs' frontend/package.json
need_grep 'devtools/comments.py' backend/devtools/lint.py
# Both halves enforce the same rule, so both gates are tested. The Python one has its own
# trap -- a `#` inside a string -- and a parser that grepped would fail this table and pass
# every file in the project, which is the failure that has no other witness.
need backend/tests/devtools/test_comments.py
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

# The conformance rules gate what renders correctly on the screen the agent is
# looking at and is wrong on one it never opens -- a colour outside the theme, a
# size outside the scale, a stroke weight decided per call site, data fetched in
# an effect. A rule that quietly stopped matching therefore looks exactly like a
# clean codebase, so each is asserted in both directions against a fixture: it
# must fire on code that violates it, and stay silent on code that only looks
# similar. The script is dependency-free, so this runs before either install and
# `make fast` catches a rule that stopped matching.
echo "==> assert the conformance checks"
CONF="$WORK/conformance"
CONFORMANCE="$OUT/frontend/devtools/conformance.mjs"
mkdir -p "$CONF/bad" "$CONF/good"

# The fixtures run from here rather than from the rendered frontend, so no package.json is
# in reach and the script falls back to its own defaults -- one of which names a theme file
# that exists only inside a project. The fixture brings its own and says so, which is also
# what keeps the project's `allow` list out of a run whose whole purpose is to prove the
# rules fire: an entry added there would otherwise silence a fixture and nothing would say
# the rule had stopped being tested.
: >"$CONF/theme.css"
conformance() { node "$CONFORMANCE" --theme-file "$CONF/theme.css" "$@"; }

# The comments in these fixtures are the point of them, not an oversight: the
# scanner has to see through a comment in both directions. A stray ")" in one
# would end an effect body early and hide the fetch below it; the word "fetch("
# in another would condemn an effect that only subscribes. Source in a generated
# project carries no comments, but nothing stops a fixture, a vendored file, or
# a project that drops the rule from carrying one.
cat >"$CONF/bad/bad.tsx" <<'BAD'
import { useEffect, useState } from "react";

export function Bad() {
  const [items, setItems] = useState<string[]>([]);
  useEffect(() => {
    fetch("/api/items").then(async (response) => setItems(await response.json()));
  }, []);
  return (
    <div className="bg-blue-500 text-white" style={{ fontSize: 13, color: "#ff0000" }}>
      <p className="text-[13px] leading-[1.7]">{items.length}</p>
    </div>
  );
}
BAD

cat >"$CONF/bad/commented.tsx" <<'BAD'
import { useEffect } from "react";

export function Commented({ load }: { load: (rows: string[]) => void }) {
  useEffect(() => {
    // an unbalanced ) in a note, which must not end this body early
    fetch("/api/rows").then(async (response) => load(await response.json()));
  }, [load]);
  return null;
}
BAD

# The two checks that read blanked source rather than raw lines are the two a
# scanner failure silences, and it silences them without a word: a regex holding
# `/*` opens a block comment that runs to the end of the file, and everything
# below it is blanked. That shipped -- conformance.mjs carried a copy of the
# comment gate's scanner with the regex handling left out, so this file reported
# `conformance ok` while both checks did nothing. Both halves now share
# devtools/scan.mjs, and frontend/tests/devtools/conformance.test.ts holds the
# table; this is the same case one tier out, where `make fast` reaches it.
cat >"$CONF/bad/regex.tsx" <<'BAD'
import { useEffect, useState } from "react";

const SLASH_OR_STAR = /[/*]/;

export function Regex() {
  const [items, setItems] = useState<string[]>([]);
  useEffect(() => {
    fetch("/api/items").then(async (response) => setItems(await response.json()));
  }, []);
  return <p style={{ fontSize: 13 }}>{SLASH_OR_STAR.source}{items.length}</p>;
}
BAD

# Tailwind's arbitrary-value syntax is the obvious way around a theme, so it is
# asserted on its own file rather than folded into the table above. The
# underscore before rgba() is the interesting part: Tailwind spells spaces that
# way inside brackets, and a word-boundary anchor treats "_rgba(" as an ordinary
# identifier and walks straight past a hard-coded colour.
#
# The last three lines are the shapes a colour utility can take besides the
# plain one, and each defeated an earlier version of these rules. A side segment
# sits between the prefix and the value (border-t-red-500, divide-y-gray-300,
# ring-offset-blue-200); a compound prefix puts the familiar word second, where
# a word-boundary anchor cannot reach it (inset-shadow-, inset-ring-,
# drop-shadow-). Both are ordinary Tailwind rather than anything exotic, which is
# what made missing them expensive.
cat >"$CONF/bad/arbitrary.tsx" <<'BAD'
export function Arbitrary() {
  return (
    <>
      <div className="bg-[#1a1a1a] border-[rgb(10,10,10)]" />
      <div className="shadow-[0_2px_4px_rgba(0,0,0,0.1)]" />
      <div className="text-[13px] leading-[1.7] font-[Inter]" />
      <div className="bg-[rebeccapurple]" style={{ color: "red" }} />
      <div className="p-[7px] gap-[3px] -mt-[0.35rem]" />
      <div className="bg-primary/80 text-subtle/60 border-destructive/50" />
      <div className="bg-primary/[0.31] ring-ring/[.08]" />
      <div className="border-t-red-500 divide-y-gray-300 ring-offset-blue-200" />
      <div className="inset-shadow-red-500 drop-shadow-xl/25 inset-ring-primary/50" />
      <div className="border-t-[rebeccapurple] text-shadow-red-500" />
    </>
  );
}
BAD

# A stroke weight can leave the theme by two doors that set the same property,
# so both are asserted: the JSX presentation prop and the utility class. Closing
# one alone leaves a check that teaches a habit it only half enforces -- and the
# two doors fail differently, which is why neither stands in for the other. A
# utility wins over the .lucide rule, because @layer utilities sorts after
# @layer base; a prop loses to it, so a prop the check misses does not render
# wrong, it silently does nothing.
cat >"$CONF/bad/stroke.tsx" <<'BAD'
import { Trash2 } from "lucide-react";

export function Stroke({ lit }: { lit: boolean }) {
  return (
    <>
      <Trash2 strokeWidth={1.5} />
      <svg className="stroke-2" />
      <circle strokeOpacity="0.5" fillOpacity={lit ? 0.4 : 0.25} />
      <path className="stroke-[.5] stroke-[-1]" />
    </>
  );
}
BAD

cat >"$CONF/bad/bad.css" <<'BAD'
.thing {
  color: #fff;
  font-family: Inter, sans-serif;
  background: rgb(10 10 10);
  stroke-width: 2;
}
BAD

# Everything here is legitimate and must pass: tokens and scale steps rather
# than literals, an effect that subscribes rather than fetches, a prop named
# after a CSS property but carrying no style, and prose a looser pattern would
# read as a palette utility.
#
# The arbitrary values here are the ones that must survive. Sizing has no scale
# to be outside of -- there is no token for "the sidebar is 240px", and across
# shadcn-ui and documenso sizing arbitraries outnumber spacing ones roughly
# thirty to one -- and an arbitrary value that reads a theme variable is the
# theme, not an escape from it.
#
# text-sm/6 is the trap in the alpha rule: a slash after a colour utility is an
# alpha modifier, but a slash after a step on the type scale is the font-size
# and line-height shorthand, and reading the second as the first would fire the
# theme check on idiomatic Tailwind. Only text- carries that shorthand, so only
# text- is exempt: shadow-lg/50 really is shadow opacity and really is caught.
#
# The other two traps are prefixes that mean something else. stroke- carries a
# colour as readily as a weight, and both stroke-(length:--icon-stroke) and
# stroke-[length:var(--icon-stroke)] read the token -- stroke-[var(--icon-stroke)]
# would set the paint instead, so the check must not teach it by example. And a
# presentation prop in type position is a type, not a style.
#
# The last two are the theme-reading forms of the arbitrary syntax, and they are
# what separates this rule from a ban on brackets. An alpha or a weight spelled
# through a custom property was declared somewhere and is reviewable there; the
# offence is the literal, not the punctuation around it.
cat >"$CONF/good/good.tsx" <<'GOOD'
import { useEffect } from "react";

const ICON_STROKE = 1.5;

type Props = { onResize: () => void; fontSize: "compact" | "comfortable" };
type Geometry = { strokeWidth: 1 | 2; strokeOpacity: 0.5 };

export function Good({ onResize, fontSize }: Props) {
  useEffect(() => {
    // no fetch( here, whatever this note says
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [onResize]);
  return (
    <div className="w-[240px] max-w-[65ch] min-h-[200px] gap-[--spacing(var(--gap))] p-4">
      <p className="text-muted-foreground bg-card/(--wash) border-t-border text-sm/6 leading-tight">
        converted-to-black, then back-to-white, text-on-black, at {fontSize}
      </p>
      <svg className="stroke-muted-foreground stroke-[length:var(--icon-stroke)]">
        <path className="stroke-(length:--icon-stroke)" strokeWidth={ICON_STROKE} />
      </svg>
    </div>
  );
}
GOOD

cat >"$CONF/bad/named-wrong.tsx" <<'BAD'
export const load = (id: string): string => id.trim();

export function SomethingElse() {
  return <p className="p-4">{load("x")}</p>;
}
BAD

conformance_output="$(conformance "$CONF/bad" 2>&1 || true)"
for rule in raw-colour palette-utility named-colour token-alpha arbitrary-spacing \
            arbitrary-type raw-type-declaration inline-type-declaration raw-stroke \
            magic-presentation-prop effect-data exported-function-expression \
            filename-export; do
    if ! printf '%s\n' "$conformance_output" | grep -q "$rule"; then
        echo "conformance check $rule did not fire" >&2
        printf '%s\n' "$conformance_output" >&2
        exit 1
    fi
done

# Named separately, because the file above already trips effect-data on an
# effect with no comment in it: without this, a scanner that stopped seeing
# through comments would still show a green table.
if ! printf '%s\n' "$conformance_output" | grep -q 'commented.tsx:.*effect-data'; then
    echo "conformance lost an effect body to a comment" >&2
    printf '%s\n' "$conformance_output" >&2
    exit 1
fi

# Named per file for the same reason: an arbitrary value is the shortest route
# out of the theme, and the table above would stay green while every one of
# them passed.
for expected in 'arbitrary.tsx:4.*raw-colour' 'arbitrary.tsx:5.*raw-colour' \
                'arbitrary.tsx:6.*arbitrary-type' 'arbitrary.tsx:7.*named-colour' \
                'arbitrary.tsx:8.*arbitrary-spacing' 'arbitrary.tsx:9.*token-alpha' \
                'arbitrary.tsx:10.*token-alpha' 'arbitrary.tsx:11.*palette-utility' \
                'arbitrary.tsx:12.*palette-utility' 'arbitrary.tsx:12.*token-alpha' \
                'arbitrary.tsx:13.*named-colour' 'arbitrary.tsx:13.*palette-utility' \
                'stroke.tsx:6.*magic-presentation-prop' 'stroke.tsx:7.*raw-stroke' \
                'stroke.tsx:8.*magic-presentation-prop' 'stroke.tsx:9.*raw-stroke' \
                'bad.css:5.*raw-stroke' \
                'regex.tsx:7.*effect-data' 'regex.tsx:10.*inline-type-declaration'; do
    if ! printf '%s\n' "$conformance_output" | grep -q "$expected"; then
        echo "conformance let an arbitrary value through: $expected" >&2
        printf '%s\n' "$conformance_output" >&2
        exit 1
    fi
done

if ! conformance "$CONF/good" >/dev/null 2>&1; then
    echo "conformance wrongly flagged legitimate code:" >&2
    conformance "$CONF/good" >&2 || true
    exit 1
fi

# The allowlist is what the failure message tells you to reach for, so a value
# that genuinely belongs outside the theme has a reviewable home. If it stops
# working the only remaining way past a false positive is deleting the check.
printf 'export const brand = "#5865f2";\n' >"$CONF/good/brand.ts"
if conformance "$CONF/good" >/dev/null 2>&1; then
    echo "conformance missed a raw colour outside the theme" >&2
    exit 1
fi
if ! conformance "$CONF/good" --allow '#5865f2' >/dev/null 2>&1; then
    echo "conformance ignored an allowlisted value" >&2
    exit 1
fi

# The stroke rule needs somewhere to point, and the hint names both halves of
# it. A token with no rule carrying it to the icons themes nothing, and a rule
# reading a token nobody declared resolves to an empty value.
need_grep 'icon-stroke: 1.5' frontend/src/index.css
need_grep '\.lucide' frontend/src/index.css
need_grep 'stroke-width: var(--icon-stroke)' frontend/src/index.css

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
# which is committed and diffed by the gate. Runtime dependencies are bounded both ways.
python3 - <<'PY'
import tomllib

deps = tomllib.load(open("backend/pyproject.toml", "rb"))["project"]["dependencies"]
unbounded = [dep for dep in deps if "<" not in dep]
assert not unbounded, f"runtime dependencies with no upper bound: {unbounded}"
PY
# A relative duration needs a uv new enough to parse one. 0.9.7 rejects "14 days"
# with a date-parsing error that names neither uv nor the version, so the floor has to
# be at least this wherever the project is installed.
need_grep 'required-version = ">=0.11.25"' backend/pyproject.toml
need_grep 'UV_EXCLUDE_NEWER' Makefile
# No lockfile ships, so a clone resolves its own unless the setup instructions say to
# commit the ones `make install` writes -- and two clones then build different trees.
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
echo "==> assert the mock build is still a mock build"
# One predicate, asserted both ways a few lines apart, so it is spelled once: the two
# directions drifting apart is exactly how a pair like this stops being a pair.
bundles_msw() { grep -rl 'setupWorker' frontend/dist/assets/*.js >/dev/null 2>&1; }
# Asserted before the production build below, and in this direction, because the plugin that
# keeps the worker out of production is one `if` away from removing it everywhere -- and
# nothing else here would notice. This script downloads no browser and runs no playwright, so
# the mock-mode e2e suite, the only other thing that needs a worker to start, is exactly the
# check that is absent. The production build runs second so `dist` is left as what ships.
run "vite build --mode mock" pnpm -C frontend build:mock
need frontend/dist/mockServiceWorker.js
bundles_msw || fail "the mock build no longer bundles msw; its e2e specs cannot pass"

run "vite build" pnpm -C frontend build

echo "==> assert the build output"
need frontend/dist/index.html
ls frontend/dist/assets/index-*.js >/dev/null
ls frontend/dist/assets/index-*.css >/dev/null
# Mock mode is a development and test concern. Shipping the worker to users would
# have it answering their requests.
! bundles_msw || fail "msw leaked into the production bundle"
# The other thing mock mode leaves behind, and the one no mode turns off by itself: public/
# is copied into every build, so this file is written and then deleted again by vite.config.ts.
need_absent frontend/dist/mockServiceWorker.js

echo "==> assert the contract artifacts are in sync with the code"
run "make openapi" make openapi
git diff --exit-code -- openapi.json frontend/src/api/schema.ts \
    || { echo "the committed contract artifacts do not match the rendered code" >&2; exit 1; }

echo "==> the contract suite, against both implementations"
# The whole point of the fullstack template: the halves are checked against each
# other, not only against themselves. Every check above passes on a project whose
# frontend cannot reach its backend at all.
run "contract suite (mocks)" pnpm -C frontend test:contract
# With the two port variables unset, so the branch that ships to users is the branch this
# exercises. This script exports a pair of its own at the top, and inheriting them meant
# contract-test.sh's own allocator -- the thing that keeps the gate green when another
# checkout is already serving :8000 -- never ran here at all.
run "contract suite (live backend)" env -u BACKEND_PORT -u FRONTEND_PORT make test-contract

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
    echo "    postgres: NO DOCKER -- backend/tests/integration/ did not run, so neither did the"
    echo "    Postgres half of the store contract. Everything about the schema that a fake"
    echo "    connection can answer did (backend/tests/store/test_schema.py), and nothing here"
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

# The same two variables dev.sh, contract-test.sh and vite.config.ts read, and the
# script exports them for the whole run, so there is no default to repeat here. Writing
# :8000 into this block again is what made it abort on another checkout's process no
# matter which ports the run was told to use.
BACKEND_PORT = os.environ["BACKEND_PORT"]
FRONTEND_PORT = os.environ["FRONTEND_PORT"]
BACKEND = f"http://localhost:{BACKEND_PORT}/tasks"
PROXY = f"http://localhost:{FRONTEND_PORT}/api/tasks"


def serving(url):
    return subprocess.run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


# Before starting anything: a port already in use makes every assertion below lie.
# dev.sh would fail to bind, the readiness probe would be answered by whatever is
# already there, and the check would report that dev.sh stranded a server -- naming
# the one bug it is here to catch, on a run where it did nothing wrong.
PORTS = ((f":{BACKEND_PORT}", BACKEND), (f":{FRONTEND_PORT}", PROXY))
already = " and ".join(n for n, u in PORTS if serving(u))
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
    die(f"dev.sh did not serve :{FRONTEND_PORT}/api within 120s")

os.write(master, b"\x03")

if not until(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid, 30):
    die("dev.sh was still running 30s after Ctrl-C")

if not until(lambda: not serving(BACKEND) and not serving(PROXY), 15):
    held = " and ".join(n for n, u in PORTS if serving(u))
    die(f"dev.sh exited but {held} still answering")
PY

echo "==> the built bundle, answered by app.serve on one origin"
# The one arrangement nothing else in this file reaches. `make test-e2e` builds the bundle
# and serves it with nothing behind it; the contract suite runs a real backend behind the
# *dev* server. So the artifact a deployment actually ships -- the built bundle, answered by
# `app.serve` -- was the combination no step exercised, and the routing it depends on is
# covered only by unit tests pointed at a fixture directory holding a stub index.html. Those
# cannot see a hashed asset name, a real `<script>` tag, or a `base` that moved.
#
# What it does NOT reach, so that claim is not read wider than it is: the bundle's own choice
# of API base. `src/api/base.ts` falls back to a relative `${BASE_URL}api` when
# VITE_API_BASE_URL is unset, and that fallback is the branch every deployment takes -- but
# the contract run below has to set the variable, because it fetches from node, where a
# relative URL has no origin to resolve against. Closing that would mean executing the built
# javascript, which means a browser, and no step here downloads one. What is proved is the
# server: that these files, at these URLs, are answered correctly on one origin.
#
# The contract suite is re-run rather than re-implemented: the same file the step above uses,
# aimed at a third edge. That is what proves the claim the whole topology rests on --
# `server.mount` strips /api exactly as the vite proxy does, so a project developed against
# one deploys onto the other. A prefix the two handled differently would pass every other
# check in this file and fail on the first deploy.
#
# Started from the venv's own interpreter rather than through `uv run`, which execs a
# launcher and spawns the server as a child: killing the pid we started would leave that
# child holding the port, and --app-dir is what makes `app` importable without a subshell
# whose pid is not the server's.
ONE_ORIGIN="http://localhost:$BACKEND_PORT"
# One URL for the pre-flight and the readiness loop both. They probed different paths and the
# guard was weaker for it: a stale `app.main` on this port answers / with a 404, so the
# pre-flight saw nothing wrong and only the readiness probe noticed.
PROBE="$ONE_ORIGIN/api/tasks"

if curl -fsS --max-time 2 "$PROBE" >/dev/null 2>&1; then
    fail ":$BACKEND_PORT is answering before app.serve started -- something else has it"
fi

# DATABASE_URL is dropped so this is the in-memory substrate: the contract suite asserts
# shapes and status codes rather than rows, and a Postgres that happened to be exported
# would make this step depend on a daemon the gate does not require.
env -u DATABASE_URL FRONTEND_BUNDLE="$OUT/frontend/dist" \
    backend/.venv/bin/python -m uvicorn --app-dir backend \
    --factory app.serve:build_server --port "$BACKEND_PORT" --log-level warning \
    >"$WORK/serve.log" 2>&1 &
serve_pid=$!

serve_deadline=$(($(date +%s) + 30))
until curl -fsS --max-time 2 "$PROBE" >/dev/null 2>&1; do
    if ! kill -0 "$serve_pid" 2>/dev/null; then
        echo "--- app.serve exited during startup ---" >&2
        cat "$WORK/serve.log" >&2
        exit 1
    fi
    [ "$(date +%s)" -lt "$serve_deadline" ] \
        || fail "app.serve did not answer $PROBE within 30s"
    sleep 1
done

# Every curl below is bounded. A server that accepts and then never answers would otherwise
# hang the whole gate, which has no outer timeout, and `|| fail` on each capture is what
# keeps a non-2xx from exiting silently under `set -e` and taking serve.log with it.
shell="$(curl -fsS --max-time 10 "$ONE_ORIGIN/")" \
    || fail "app.serve did not answer / at all"
printf '%s' "$shell" | grep -q '<div id="root">' \
    || fail "app.serve answered / with something that is not the built shell"

# A reload on a client route has to reach the same document. This failing while the dev
# server works is exactly the divergence between the two edges that this step exists to deny.
deep="$(curl -fsS --max-time 10 "$ONE_ORIGIN/some/client/route")" \
    || fail "app.serve refused a client route instead of falling back to the shell"
[ "$deep" = "$shell" ] || fail "a deep link did not fall back to the built index.html"

# Every file the shell asks for, at the exact URL it asks for it -- leading slash and `base`
# and all -- read out of the document so this follows the build rather than needing an edit
# each time a hash moves. Scripts and stylesheets both: they are hashed the same way and a
# `base` moves them together.
#
# Matching only the tail was tried and is worthless: with a `base` this server does not serve
# from, `assets/index-*.js` still resolves because the file is on disk under that name, while
# the browser asks for `/app/assets/...`, misses, and is handed the shell by the fallback. So
# the answer is checked by type, not by status -- a script tag given a page of HTML is a 200,
# and it is the whole failure this step exists to see.
# Collected before the loop rather than piped into it, because a `for` over nothing runs no
# body and reports success: a build that stopped naming its assets the expected way would
# turn this check off rather than fail it.
refs="$(printf '%s' "$shell" | grep -oE '(src|href)="[^"]*\.(js|css)"' \
            | sed 's/^[a-z]*="//; s/"$//')"
[ -n "$refs" ] || fail "the built index.html names no script or stylesheet to load"

for ref in $refs; do
    case "$ref" in
        *.js)  want=javascript ;;
        *.css) want=css ;;
        *)     continue ;;
    esac
    ref_type="$(curl -s --max-time 10 -o /dev/null -w '%{content_type}' "$ONE_ORIGIN$ref")"
    case "$ref_type" in
        *"$want"*) ;;
        *) fail "the shell asks for $ref and app.serve answered it as ${ref_type:-nothing}" ;;
    esac
done

# An asset name carries the build's hash, so one the bundle does not hold is a stale
# document. Answering it with the shell hands a script tag a page of HTML, which fails as a
# syntax error a long way from the deploy that caused it.
stale_status="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' \
    "$ONE_ORIGIN/assets/index-deadbee.js")"
[ "$stale_status" = "404" ] \
    || fail "a hashed asset the bundle lacks answered $stale_status, not 404"

# The package script rather than the spec's path, so renaming that file is a change in one
# place the generated project owns rather than a break in the generator's gate.
run "contract suite (app.serve)" env CONTRACT_TARGET=live VITE_API_BASE_URL="$ONE_ORIGIN/api" \
    pnpm -C frontend test:contract

kill "$serve_pid" 2>/dev/null || true
wait "$serve_pid" 2>/dev/null || true
serve_pid=""

echo "==> assert the drift gate is two-sided, on both halves"
# A ratchet that only ever rises is not a ratchet. Whatever a tree improved since its
# baseline was recorded is slack, and slack is added to what the next commit may spend,
# so the second side is what bounds the first. The two directions have opposite
# handling -- a rise is refused, a fall is recorded for you -- and only one of them may
# rewrite the file, so no single case can show the rule. Hence a table per half.
#
# Both run against scratch trees rather than the generated project: the frontend needs
# real density and enough of it to clear the size guard, and the backend needs an exact
# mean rather than whatever the smoke project happens to score.
(
    cd "$WORK"
    mkdir -p drift/src && cd drift
    export PATH="$OUT/frontend/node_modules/.bin:$PATH"
    node -e '
    let out = "";
    for (let i = 0; i < 600; i++) {
        out += `export function f${i}(a: number, b: number) {\n  if (a > 0) {\n    if (b > 0) {\n      return 1;\n    }\n  }\n  return 0;\n}\n`;
    }
    require("node:fs").writeFileSync("src/many.ts", out);'

    node "$OUT/frontend/devtools/complexity.mjs" src --baseline measured.json \
        --update-baseline >/dev/null

    # State the cases as multiples of the measured level rather than as numbers, so the
    # table keeps meaning what it says if biome's scoring ever moves. Below 1 is a tree
    # that got worse; above 1, one that improved since.
    seed='const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8"));
    const density = Number((measured.density * Number(process.argv[1])).toFixed(3));
    fs.writeFileSync("baseline.json", JSON.stringify({ ...measured, density, origin: density }, null, 2));'

    settled='const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8")).density;
    const want = process.argv[2] === "tree"
        ? measured
        : Number((measured * Number(process.argv[1])).toFixed(3));
    const got = JSON.parse(fs.readFileSync("baseline.json", "utf8")).density;
    if (got !== want) { console.error(`baseline holds ${got}, wanted ${want}`); process.exit(1); }'

    # baseline, as a multiple of the tree | flags | expected exit | baseline afterwards
    # The frontend tolerance is 2%: 0.99 and 1.01 sit inside it, 0.90 and 1.05 outside.
    # The last case is the one worth having -- the flag lowers a stale baseline, and
    # must never be a way to accept a rise.
    while IFS='|' read -r factor flags expect after; do
        [ -n "$factor" ] || continue
        node -e "$seed" "$factor"
        rc=0
        # shellcheck disable=SC2086
        node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json $flags \
            >/dev/null 2>&1 || rc=1
        if [ "$rc" != "$expect" ]; then
            echo "frontend drift: baseline at ${factor}x with '$flags' exited $rc, wanted $expect" >&2
            exit 1
        fi
        if ! node -e "$settled" "$factor" "$after"; then
            echo "frontend drift: baseline at ${factor}x with '$flags' should have left '$after'" >&2
            exit 1
        fi
    done <<'DRIFT_CASES'
0.99|--tighten-baseline|0|same
1.01|--tighten-baseline|0|same
1.05|--tighten-baseline|0|tree
1.05||1|same
0.90||1|same
0.90|--tighten-baseline|1|same
DRIFT_CASES

    # An exit code cannot tell the two failures apart, and they ask for opposite things:
    # one for a refactor, the other for a one-line commit of the file.
    node -e "$seed" 1.05
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json 2>&1 \
        | grep -q 'baseline is .* above the tree'
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline tightened'

    # Tightening lowers the drift reference and must leave the ceiling's anchor alone.
    # Moving origin down too would let a codebase that improves and then regresses walk
    # past the ceiling one recorded improvement at a time. This half of the rule exists
    # on the frontend only: the backend's ceiling is an absolute constant, so its
    # baseline carries nothing to preserve.
    node -e '
    const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8")).density;
    const after = JSON.parse(fs.readFileSync("baseline.json", "utf8"));
    if (after.density !== measured) throw new Error("tightening did not lower the density");
    if (after.origin === measured) throw new Error("tightening moved the ceiling anchor");
    '
)

# Density falling to zero returns before the drift check, so it is the one improvement
# the two-sided rule could not see -- and the slack it leaves is not a fraction of the
# baseline but the whole of it. Reintroducing the complexity later would then measure
# against a number describing a codebase that no longer existed, and pass. The flat
# fixture is the branching one with the branches taken out, so the only thing that
# differs between them is the density.
(
    cd "$WORK"
    mkdir -p drift-zero/src && cd drift-zero
    export PATH="$OUT/frontend/node_modules/.bin:$PATH"

    branching='let out = "";
    for (let i = 0; i < 600; i++) {
        out += `export function f${i}(a: number, b: number) {\n  if (a > 0) {\n    if (b > 0) {\n      return 1;\n    }\n  }\n  return 0;\n}\n`;
    }
    require("node:fs").writeFileSync("src/many.ts", out);'

    flat='let out = "";
    for (let i = 0; i < 600; i++) {
        out += `export function f${i}(a: number, b: number) {\n  return a + b;\n}\n`;
    }
    require("node:fs").writeFileSync("src/many.ts", out);'

    node -e "$branching"
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --update-baseline >/dev/null
    node -e "$flat"

    # Without the flag this is a notice, not a failure: there is nothing to gate while
    # the metric is zero, and the checking half must never rewrite a file.
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json >/dev/null
    node -e '
    const density = JSON.parse(require("node:fs").readFileSync("baseline.json", "utf8")).density;
    if (density === 0) throw new Error("the checking half rewrote the baseline");
    '

    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline tightened'
    node -e '
    const after = JSON.parse(require("node:fs").readFileSync("baseline.json", "utf8"));
    if (after.density !== 0) throw new Error("an improvement to zero was not recorded");
    if (!(after.origin > 0)) throw new Error("tightening to zero moved the ceiling anchor");
    '

    # The point of recording it: putting the complexity back is now a rise from zero,
    # refused and sent to `pnpm complexity:baseline`, rather than a free ride against a
    # baseline describing code nobody kept.
    node -e "$branching"
    if node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        >/dev/null 2>&1; then
        echo "complexity reintroduced after a fall to zero passed against a stale baseline" >&2
        exit 1
    fi
)

# The backend table, in its own unit. Run against the generated project's interpreter:
# complexity.py needs tomllib, so unlike the agent guard it is not a stdlib-only script
# that any python3 on PATH can host.
CX="$WORK/complexity"
mkdir -p "$CX"

# Stand in for ruff, so the measured mean is an exact known number rather than whatever
# the smoke-test project happens to score.
cat >"$CX/fake-ruff" <<'FAKE_RUFF'
#!/bin/sh
echo '[{"message":"a is too complex (2 > 0)"},{"message":"b is too complex (2 > 0)"}]'
FAKE_RUFF
chmod +x "$CX/fake-ruff"

# Compare a recorded baseline mean against a number: <file> <number> eq|lt.
compare='import json,operator,sys
recorded = json.load(open(sys.argv[1]))["mean"]
sys.exit(not getattr(operator, sys.argv[3])(recorded, float(sys.argv[2])))'

# baseline mean | flags | expected exit | expected baseline afterwards
# The tree measures 2.000 and the backend tolerance is 0.050.
while IFS='|' read -r before flags expect after; do
    [ -n "$before" ] || continue
    printf '{"callables": 2.0, "mean": %s, "p90": 2.0}\n' "$before" >"$CX/baseline.json"
    rc=0
    # shellcheck disable=SC2086
    (cd "$CX" && "$OUT/backend/.venv/bin/python" "$OUT/backend/devtools/complexity.py" . \
        --ruff "$CX/fake-ruff" --baseline baseline.json --min-callables 1 $flags) \
        >/dev/null 2>&1 || rc=1
    if [ "$rc" != "$expect" ]; then
        echo "backend drift: baseline $before with '$flags' exited $rc, wanted $expect" >&2
        exit 1
    fi
    if ! python3 -c "$compare" "$CX/baseline.json" "$after" eq; then
        echo "backend drift: baseline $before with '$flags' should have left $after" >&2
        exit 1
    fi
done <<'DRIFT_CASES'
1.970|--tighten-baseline|0|1.970
2.030|--tighten-baseline|0|2.030
1.900|--tighten-baseline|1|1.900
1.900||1|1.900
2.400||1|2.400
2.400|--tighten-baseline|0|2.000
DRIFT_CASES

echo "==> assert each half's fixing variant tightens and its checking variant refuses"
# Tightening only means anything if exactly one of each half's two lint entry points
# asks for it, and grepping for the flag cannot tell those apart -- a flag wired into
# both would leave `make lint-check`, and so the pre-commit hook, silently accepting
# stale baselines, which is the state this whole check exists to end. So drive both.
#
# Last, and not a line earlier. The fixing variants run `biome check --write`,
# `ruff --fix`, `ruff format` and `codespell --write-changes` across the generated project:
# anywhere above this they would repair whatever an earlier step exists to catch, move
# backend source out from under `make openapi-check`, and hand back a green run on a
# template that ships unformatted source. Nothing may follow this section.
node -e '
const pkg = JSON.parse(require("node:fs").readFileSync("frontend/package.json", "utf8"));
if (!pkg.scripts.lint.includes("--tighten-baseline")) throw new Error("pnpm lint cannot tighten");
if (pkg.scripts["lint:check"].includes("--tighten-baseline")) throw new Error("lint:check tightens");
'

# The rendered halves are both far below their size guards, so drift stands down on
# them. Pad the frontend's src/ until it does not, in files under the 500-line gate --
# biome runs first, and one long file would stop the lint before complexity ever ran.
node -e '
let body = "";
for (let index = 0; index < 60; index++) {
    body += `export function f${index}(a: number, b: number) {\n  if (a > 0) {\n    if (b > 0) {\n      return 1;\n    }\n  }\n  return 0;\n}\n`;
}
const fs = require("node:fs");
fs.mkdirSync("frontend/src/pad", { recursive: true });
for (let file = 0; file < 10; file++) fs.writeFileSync(`frontend/src/pad/pad${file}.ts`, body);
'
run "frontend baseline" pnpm -C frontend run complexity:baseline
pnpm -C frontend run complexity | grep -q 'drift ok'   # the padded half really is gating

INFLATED="$(node -e '
const fs = require("node:fs");
const path = "frontend/.complexity-baseline.json";
const baseline = JSON.parse(fs.readFileSync(path, "utf8"));
const density = baseline.density * 1.05;
fs.writeFileSync(path, JSON.stringify({ ...baseline, density }, null, 2));
process.stdout.write(String(density));
')"

# Assert on the gate's own message rather than on the exit status alone: each half's
# lint runs five or six tools, so a bare nonzero would let tsc or basedpyright stand in
# for a refusal that never happened.
if out="$(pnpm -C frontend lint:check 2>&1)"; then
    fail "pnpm lint:check accepted a baseline standing above the tree"
fi
printf '%s\n' "$out" | grep -q 'baseline is .* above the tree' || {
    echo "pnpm lint:check failed, but not on the stale baseline:" >&2
    printf '%s\n' "$out" >&2
    exit 1
}

if ! out="$(pnpm -C frontend lint 2>&1)"; then
    echo "pnpm lint failed instead of tightening the stale baseline:" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi
printf '%s\n' "$out" | grep -q 'baseline tightened'
node -e '
const fs = require("node:fs");
const density = JSON.parse(fs.readFileSync("frontend/.complexity-baseline.json", "utf8")).density;
if (!(density < Number(process.argv[1]))) {
    throw new Error("lint reported tightening but left the baseline on disk");
}
' "$INFLATED"

# A missing baseline above the floor is a failure, not a notice. This half used to print
# "no baseline yet" and return 0, so the drift check never turned on in any project where
# nobody thought to record one -- a check that silently never starts. Asserted here because
# the shipped baseline means the passing path is the only one anyone would otherwise see.
mv backend/.complexity-baseline.json "$WORK/baseline.parked"
if (cd backend && uv run --no-sync python devtools/complexity.py app \
        --baseline .complexity-baseline.json >/dev/null 2>&1); then
    fail "backend complexity accepted a missing baseline above min-callables"
fi
mv "$WORK/baseline.parked" backend/.complexity-baseline.json

# The backend half of the same drive. Its floor moves so this fixture's own tiny baseline
# is the thing under test rather than the smoke project's real callable count.
sed 's/^min-callables = 50$/min-callables = 1/' backend/pyproject.toml >"$WORK/pyproject.floored"
cp "$WORK/pyproject.floored" backend/pyproject.toml
need_grep '^min-callables = 1$' backend/pyproject.toml
printf '{"callables": 1.0, "mean": 2.9, "p90": 1.0}\n' >backend/.complexity-baseline.json

# --no-sync throughout this block: pyproject.toml was just edited, and a re-resolve
# against the registry is neither wanted here nor relevant to what is being asserted.
if out="$(cd backend && uv run --no-sync python devtools/lint.py --check 2>&1)"; then
    fail "backend lint --check accepted a baseline recorded far above the tree"
fi
printf '%s\n' "$out" | grep -q 'FAIL: baseline is .* above the tree' || {
    echo "backend lint --check failed, but not on the stale baseline:" >&2
    printf '%s\n' "$out" >&2
    exit 1
}

if ! out="$(cd backend && uv run --no-sync python devtools/lint.py 2>&1)"; then
    echo "backend lint failed instead of tightening the stale baseline:" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi
printf '%s\n' "$out" | grep -q 'baseline tightened'
python3 -c "$compare" backend/.complexity-baseline.json 2.9 lt \
    || fail "backend lint reported tightening but left the baseline on disk"

echo "==> OK ($VARIANT)"
