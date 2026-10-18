#!/bin/sh
set -eu

unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

VARIANT="${1:-default}"
FAST="${FAST:-0}"
export UV_EXCLUDE_NEWER="7 days"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RENDER="$REPO/devtools/render.sh"
COPIER_SPEC="$(sh "$RENDER" --spec)"

fail() { echo "check: $*" >&2; exit 1; }
need() { [ -f "$1" ] || fail "expected file: $1${2:+. $2}"; }
need_exec() { [ -x "$1" ] || fail "expected executable file: $1${2:+. $2}"; }
need_absent() { [ ! -e "$1" ] || fail "expected no such file: $1${2:+. $2}"; }
need_grep() { grep -q "$1" "$2" || fail "expected /$1/ in $2${3:+. $3}"; }
need_no_grep() { ! grep -q "$1" "$2" || fail "unexpected /$1/ in $2${3:+. $3}"; }
need_only() {
    kind="$1" dir="$2"
    shift 2
    listed="$(mktemp)"
    printf '%s\n' "$@" | LC_ALL=C sort >"$listed"
    compared=0
    drift="$(find "$dir" -mindepth 1 -maxdepth 1 -type "$kind" ! -name __pycache__ |
        sed "s|^$dir/||" | LC_ALL=C sort | diff "$listed" -)" || compared=$?
    rm -f "$listed"
    [ "$compared" -le 1 ] || fail "diff could not compare $dir with the list in check_template.sh"
    [ -z "$drift" ] || fail "$dir does not hold exactly what check_template.sh names (< named, > found). Without this, an entry nobody named passes unseen:
$drift"
}

shared_prose() {
    sed -E -n "/^## Approach/,/^## (Layout|The template is inert)/p" "$1" |
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
for heading in Approach 'Fail loudly' 'Zero comments' 'Simplified technical English'; do
    grep -q "^## $heading\$" "$PROSE_ROOT" && grep -q "^## $heading\$" "$PROSE_TEMPLATE" ||
        fail "the shared prose range in check_template.sh does not reach '## $heading'. A range that stops early passes over the drift it does not reach."
done
rm -f "$PROSE_ROOT" "$PROSE_TEMPLATE"

cmp -s "$REPO/devtools/install-hooks.sh" "$REPO/template/devtools/install-hooks.sh" ||
    fail "devtools/install-hooks.sh and template/devtools/install-hooks.sh differ. Keep them identical: make hooks runs the root copy, and this script exercises only the template's."

STALE="$(grep -rEoh 'copier@[0-9]+\.[0-9]+\.[0-9]+' "$REPO" \
    --include='*.md' --include='*.jinja' --include='*.sh' --include='*.yml' \
    2>/dev/null | sort -u | grep -v "^$COPIER_SPEC\$" || true)"
if [ -n "$STALE" ]; then
    echo "check: this repo pins $COPIER_SPEC, but also names:" >&2
    echo "$STALE" | sed 's/^/check:   /' >&2
    echo "check: every copier@ in the repo is a command someone runs. Bump them together." >&2
    echo "check: devtools/render.sh owns the pin. A stale copy generates with a release this gate never ran." >&2
    exit 1
fi

for tool in uv git python3 node pnpm curl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "check: $tool is required and not on PATH." >&2
        echo "check: see template/docs/installation.md for how to install it." >&2
        exit 1
    fi
done

PORTS="$(python3 - "${BACKEND_PORT:+named}" "${BACKEND_PORT:-8000}" \
                   "${FRONTEND_PORT:+named}" "${FRONTEND_PORT:-5173}" <<'PY'
import socket
import sys

LOOPBACKS = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))


def free(port):
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

cleanup() {
    status="${1:-$?}"
    if [ -n "${cleaned:-}" ]; then
        exit "$status"
    fi
    cleaned=yes

    if [ -n "${serve_pid:-}" ]; then
        kill "$serve_pid" 2>/dev/null || true
        wait "$serve_pid" 2>/dev/null || true
    fi

    leaked="$(processes_under_work)"
    if [ -n "$leaked" ]; then
        echo "check: a process outlived the step that started it:" >&2
        printf '%s\n' "$leaked" >&2
        echo "check: stop it where it is started. A server still holding this tree rewrites it" >&2
        echo "       while this removes it, and it holds its port after the run." >&2
        printf '%s\n' "$leaked" | while read -r pid _; do
            kill -KILL "$pid" 2>/dev/null || true
        done
        if [ "$status" -eq 0 ]; then
            status=1
        fi
    fi

    if ! rm -rf "$WORK" 2>/dev/null; then
        echo "check: $WORK could not be removed and is left behind." >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

processes_under_work() {
    if [ -z "${WORK:-}" ]; then
        echo "check: WORK is unset, so nothing can be matched against it. An empty grep -F pattern matches every process, and the handler kills what it matches." >&2
        exit 1
    fi
    if ! running="$(ps -eo pid=,args=)"; then
        echo "check: ps failed, so no leaked process can be found or ruled out. A ps that fails reports no leak, and a check that cannot fail proves nothing." >&2
        exit 1
    fi
    printf '%s\n' "$running" | grep -F "$WORK" || true
}

need_nothing_outlived() {
    still="$(processes_under_work)"
    if [ -n "$still" ]; then
        echo "check: $1 left a process running, and no other check here sees it:" >&2
        printf '%s\n' "$still" >&2
        exit 1
    fi
}

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
need docs/adr/README.md "AGENTS.md tells an agent to write decisions in docs/adr/, so a fresh project has that directory"
need .copier-answers.yml
need_grep '_src_path' .copier-answers.yml
need_grep '_commit' .copier-answers.yml
need_exec .githooks/pre-commit "Rendering can drop the executable bit, and a hook without it never runs and says nothing"
need_exec devtools/install-hooks.sh "Rendering can drop the executable bit"
need_exec devtools/dev.sh "Rendering can drop the executable bit"
need_exec devtools/contract-test.sh "Rendering can drop the executable bit"

echo "==> assert every document is named, and names only documents that exist"
python3 "$REPO/devtools/links_test.py" \
    || fail "links.py stopped recognising a citation, so the two sweeps below would prove nothing."
python3 "$REPO/devtools/links.py" "$REPO" --exclude template --exclude devtools/links_test.py \
    --allow-orphan README.md --allow-orphan CLAUDE.md \
    --allow-orphan .github/PULL_REQUEST_TEMPLATE.md \
    || fail "a document names something that does not exist, or nothing names it. Only README.md, CLAUDE.md and the pull request template are read by filename, so only they may be orphans."
python3 "$REPO/devtools/links.py" "$OUT" \
    --allow-orphan README.md --allow-orphan CLAUDE.md \
    || fail "the generated project names a document that does not exist, or orphans one."

echo "==> assert decision records are numbered from 0001 with no gaps"
numbered_without_gaps() {
    expected=1
    for record in "$1"/[0-9][0-9][0-9][0-9]-*.md; do
        [ -e "$record" ] || return 0
        want="$(printf '%04d' "$expected")"
        [ "$(basename "$record" | cut -c1-4)" = "$want" ] \
            || fail "$1: record $want is next, and $(basename "$record") is there. The numbers are a reading order (docs/adr/README.md). Renumber, and update every citation in the tree in the same change."
        expected=$((expected + 1))
    done
}
GAPPED="$(mktemp -d)"
touch "$GAPPED/0001-a.md" "$GAPPED/0003-c.md"
if (numbered_without_gaps "$GAPPED") 2>/dev/null; then
    fail "numbered_without_gaps passed a gap, so the sweep below would prove nothing."
fi
rm -rf "$GAPPED"
for records in "$REPO/docs/adr" "$OUT/docs/adr/template" "$OUT/docs/adr"; do
    numbered_without_gaps "$records"
done

echo "==> assert the code the template owns stands without the identity stub"
python3 - "$REPO/copier.yml" "$OUT/backend" <<'PY' || fail "code the template owns depends on the identity stub. Under identity_stub=false a project keeps only principal_for and the shared module, so that code fails on its first update."
import ast
import re
import sys
from pathlib import Path

copier, backend = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
owned = set(re.findall(r"not (?:identity_stub|example_resource) and _copier_operation == 'update' %\}backend/([^{]+)\{%", copier))
if not owned:
    sys.exit("check: copier.yml names no file for identity_stub, so this check would pass over nothing")
found = []
WHOLE = "tests/identity/test_shared.py"
def under(name, prefix):
    return name == prefix or name.startswith(prefix + ".")
for area, allowed in (("tests", {"shared"}), ("app", {"shared", "principal_for"}), ("devtools", {"shared", "principal_for"})):
    for module in sorted((backend / area).rglob("*.py")):
        if str(module.relative_to(backend)) in owned:
            continue
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{one.name}" for one in node.names)]
            elif isinstance(node, ast.Import):
                names = [one.name for one in node.names]
            else:
                continue
            hits = []
            if area == "tests":
                hits = [name for name in names if under(name, "app.routes.tenant") or under(name, "tests.identity")]
            if isinstance(node, ast.ImportFrom) and node.module == "app.identity":
                hits += [f"app.identity.{one.name}" for one in node.names if one.name not in allowed]
            whole = (isinstance(node, ast.Import) and "app.identity" in names) or (
                isinstance(node, ast.ImportFrom) and node.module == "app" and any(one.name == "identity" for one in node.names)
            )
            if whole and str(module.relative_to(backend)) != WHOLE:
                hits.append("app.identity")
            if hits:
                found.append(f"{module.relative_to(backend)}:{node.lineno} imports {hits[0]}")
for line in found:
    print(f"check: {line}, which a project that replaced the identity seam does not have", file=sys.stderr)
sys.exit(1 if found else 0)
PY

echo "==> assert every workflow is valid, here and in what ships"
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

USES='^[[:space:]]*-?[[:space:]]*uses:[[:space:]]*'
: >"$WORK/action-pins"
for workflows in "$REPO/.github/workflows" "$OUT/.github/workflows"; do
    [ -d "$workflows" ] || fail "expected workflows in $workflows, and the directory is absent."
    find "$workflows" \( -name '*.yml' -o -name '*.yaml' \) -print >"$WORK/workflow-list"
    [ -s "$WORK/workflow-list" ] || fail "$workflows holds no workflow, so this check checked nothing."
    xargs "$ACTIONLINT_DIR/actionlint" <"$WORK/workflow-list" \
        || fail "a workflow in $workflows is invalid, and a broken workflow cannot report its own breakage."
    while IFS= read -r workflow; do
        unpinned="$(grep -nE "$USES" "$workflow" | grep -vE '@[0-9a-f]{40}' || true)"
        [ -z "$unpinned" ] || fail "$workflow uses an action that is not pinned to a commit. A tag moves, and the token the action runs under can read the repository: $unpinned"
        grep -hE "$USES" "$workflow" | sed -E "s/$USES//; s/@([0-9a-f]{40}).*/ \\1/" >>"$WORK/action-pins"
    done <"$WORK/workflow-list"
done

[ -s "$WORK/action-pins" ] || fail "found no action pinned in either tree, so there was nothing to compare."
split="$(sort -u "$WORK/action-pins" | awk '{ seen[$1]++ } END { for (a in seen) if (seen[a] > 1) print a }' | sort | tr '\n' ' ')"
[ -z "$split" ] || fail "pinned to more than one commit across both trees: ${split% }. Pin every use of that action to one commit; .github/dependabot.yml bumps both trees in one pull request. Nothing here runs the workflow that ships, so its stale pin goes unseen."

uncommented() { sed 's/#.*//' "$1"; }
uncommented "$REPO/.github/workflows/check.yml" | grep -q 'make check' \
    || fail "check.yml no longer runs 'make check' outside a comment. It names the target the hook runs; it does not re-spell the steps."
if uncommented "$REPO/.github/workflows/check.yml" | grep -q 'check_template.sh'; then
    fail "check.yml calls check_template.sh directly. Name the make target, so the workflow and the hook cannot run different things."
fi
uncommented "$REPO/.github/workflows/release.yml" | grep -q 'devtools/version.sh' \
    || fail "release.yml no longer reads VERSION through devtools/version.sh, so the validation it depends on is a second copy that can drift from this one."

uncommented "$REPO/.github/workflows/release.yml" | grep -q 'workflow_run:' \
    || fail "release.yml no longer waits on a workflow_run. It must run after the gate, never beside it: a tag cut from an unchecked tree is what this trigger exists to prevent."
if uncommented "$REPO/.github/workflows/release.yml" | grep -qE '^[[:space:]]*push:'; then
    fail "release.yml triggers on a push again, which races the gate and can publish before it passes."
fi
uncommented "$REPO/.github/workflows/release.yml" | grep -q "conclusion == 'success'" \
    || fail "release.yml no longer refuses a failed gate, so a red check would still cut a tag."

for workflow in "$REPO"/.github/workflows/*.yml; do
    if uncommented "$workflow" | grep -qE 'pull-requests:[[:space:]]*write|gh pr merge|--auto\b'; then
        fail "$workflow can act on a pull request. No workflow here merges or approves: the gate says a change may land, and a person decides that it does."
    fi
done

echo "==> assert VERSION names a version a release could be cut from"
CLAIMED="$(sh "$REPO/devtools/version.sh" "$REPO")" \
    || fail "devtools/version.sh refuses VERSION, so release.yml would refuse it after the merge."
for rejected in '0.5.0oops' '0.5.0.1' '1.2' '01.2.3' 'latest' '' 'v0.5.0'; do
    printf '%s' "$rejected" >"$WORK/VERSION"
    if sh "$REPO/devtools/version.sh" "$WORK" >/dev/null 2>&1; then
        fail "devtools/version.sh accepted '$rejected', which is not a semantic version. release.yml would cut a permanent tag from it."
    fi
done
rm -f "$WORK/VERSION"

NEWEST_RELEASED="$(git -C "$REPO" tag -l 'v*' | sed 's/^v//' | sort -V | tail -1)"
if [ -z "$NEWEST_RELEASED" ]; then
    echo "    tags: NONE VISIBLE -- nothing proves $CLAIMED is not a version already released."
    echo "    A shallow checkout carries no tags; this repo has them, so a run reporting this is"
    echo "    a run whose checkout is wrong rather than a repo before its first release."
else
    LOWEST="$(printf '%s\n%s\n' "$NEWEST_RELEASED" "$CLAIMED" | sort -V | head -1)"
    if [ "$CLAIMED" != "$NEWEST_RELEASED" ] && [ "$LOWEST" = "$CLAIMED" ]; then
        fail "VERSION claims $CLAIMED and v$NEWEST_RELEASED is already released. A version never goes backwards: release.yml would find the release, stop, and ship nothing."
    fi
    echo "    version: $CLAIMED, newest released v$NEWEST_RELEASED"
fi

if [ "$VARIANT" = "default" ]; then
    echo "==> assert hostile answers stay data, not syntax"
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

for label, got, want in (
    ("package.json description", pkg["description"], desc),
    ("package.json author", pkg["author"], {"name": author, "email": email}),
    ("package.json repository url", pkg["repository"]["url"],
     f"git+https://github.com/{org}/smoke-test.git"),
    ("pyproject description", project["description"], f"{desc} -- backend"),
    ("pyproject authors", project["authors"], [{"name": author, "email": email}]),
):
    if got != want:
        die(f"{label} came out as {got!r}, not {want!r}. A value can parse and still come out mangled.")
PY

    echo "==> assert a fresh copy ships every file a question can stop an update bringing"
    DECLINED="$(sh "$RENDER" --into "$WORK/declined" -- \
        --data example_resource=false --data identity_stub=false)"
    for question in example_resource identity_stub; do
        OWNED="$(sed -n "s/.*not $question and _copier_operation == 'update' %}\([^{]*\){% endif %}.*/\1/p" "$REPO/copier.yml")"
        [ -n "$OWNED" ] || fail "copier.yml names no file for $question, so this check would pass over nothing"
        for kept in $OWNED; do
            [ -f "$DECLINED/$kept" ] || fail "a copy with $question=false left out $kept. The store, the schema, the frontend and the routes import it, so a copy without it does not import."
        done
        grep -q "^$question: false$" "$DECLINED/.copier-answers.yml" \
            || fail "$question=false is not recorded, so the next update would bring its files back"
    done

    echo "==> assert a project that declines keeps its own files through an update"
    G="git -c user.email=check@example.com -c user.name=check -c commit.gpgsign=false"
    BASE="$(sh "$RENDER" --into "$WORK/update")"
    MARK="kept by the project"
    OWNED="$(sed -n "s/.*not [a-z_]* and _copier_operation == 'update' %}\([^{]*\){% endif %}.*/\1/p" "$REPO/copier.yml")"
    (
        cd "$BASE"
        git init -q && $G add -A && $G commit -qm copy
        for owned in $OWNED; do echo "$MARK" >>"$owned"; done
        sed -i.bak -e 's/^example_resource: true$/example_resource: false/' \
            -e 's/^identity_stub: true$/identity_stub: false/' .copier-answers.yml
        rm .copier-answers.yml.bak
        grep -q '^example_resource: false$' .copier-answers.yml \
            && grep -q '^identity_stub: false$' .copier-answers.yml \
            || fail "the answers were not recorded false, so the update below declines nothing"
        $G commit -qam "the project replaces the example and the stub"
    )
    for owned in $OWNED; do
        [ -f "$WORK/update/src/template/$owned" ] || fail "copier.yml owns $owned, which the template does not have"
        echo "a later template change" >>"$WORK/update/src/template/$owned"
    done
    echo "a later template change" >>"$WORK/update/src/template/docs/schema.md"
    $G -C "$WORK/update/src" commit -qam "a later template"
    (cd "$BASE" && uvx --exclude-newer "7 days" "$COPIER_SPEC" update --defaults --quiet \
        --trust --vcs-ref=HEAD) >"$WORK/update.log" 2>&1 \
        || { cat "$WORK/update.log" >&2; fail "copier update failed on a project that declined both questions"; }
    grep -q "a later template change" "$BASE/docs/schema.md" \
        || fail "the update did not bring the later template, so the check below proves nothing"
    for owned in $OWNED; do
        [ -f "$BASE/$owned" ] || fail "an update after declining deleted $owned. template/docs/installation.md records the answer in .copier-answers.yml first, so the old render does not hold the file either."
        [ "$(tail -1 "$BASE/$owned")" = "$MARK" ] || fail "an update after declining overwrote $owned"
        ! grep -q "a later template change" "$BASE/$owned" \
            || fail "an update after declining still brought the template's change to $owned"
    done

    echo "==> assert a copy without the GitHub workflow names no workflow"
    NO_CI="$(sh "$RENDER" --into "$WORK/no-ci" -- --data github_ci=false)"
    [ ! -e "$NO_CI/.github" ] || fail "a copy with github_ci=false still ships .github/"
    [ ! -e "$NO_CI/backend/tests/test_workflow.py" ] \
        || fail "a copy with github_ci=false still ships the test of a workflow it does not have"
    grep -q '^github_ci: false$' "$NO_CI/.copier-answers.yml" \
        || fail "github_ci=false is not recorded, so the next update would bring the workflow back"
    named="$(cd "$NO_CI" && grep -rIl --exclude-dir=adr 'ci\.yml' .)" \
        && fail "a copy with github_ci=false still names ci.yml in: $(echo $named). A doc or script that names it promises a workflow the project does not have."
    [ $? -eq 1 ] || fail "grep could not read the copy with github_ci=false"
fi

echo "==> assert the agent guard"
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
        echo "the agent guard allowed a command it must deny: $cmd. A miss lets a catastrophic command through." >&2
        exit 1
    fi
done

echo "$MUST_ALLOW" | while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    if guard_denies "$cmd"; then
        echo "the agent guard denied a command it must allow: $cmd. A false hit teaches agents to route around the guard." >&2
        exit 1
    fi
done

echo "==> assert the shape of both halves"
need Makefile
for target in install hooks pre-commit gate secrets lint lint-check test test-fast test-contract \
              test-e2e test-e2e-live db-test observe observe-test db db-demo migrate roles \
              schema dev \
              dev-frontend dev-backend \
              openapi openapi-check build upgrade clean; do
    grep -q "^$target:" Makefile || { echo "Makefile is missing the $target target" >&2; exit 1; }
done
need_grep '^gate: secrets lint-check openapi-check test$' Makefile
need_no_grep '^gate:.*test-e2e' Makefile "A gate that downloads a browser is a gate people learn to commit around."
need_no_grep '^gate:.*db-test' Makefile
need_no_grep '^gate:.*observe-test' Makefile
need_no_grep '^gate:.*test-contract-db' Makefile
need_exec devtools/gate.sh
need_exec devtools/worktree-tree.sh
need_exec devtools/hold-the-gate.pl
need_grep 'DATABASE_URL=postgres://app_app' Makefile "The contract suite on Postgres serves as the application role. A superuser bypasses every policy, so the suite would pass against a database with no isolation."
need backend/tests/test_gate.py
need_grep '^norecursedirs = \["integration", "observe"\]' backend/pyproject.toml "A test that needs a server lives in a tier. A test that skips itself exits 0 like a test that passed."
for banned in logging structlog opentelemetry; do
    grep -q "^\"$banned\".msg = " backend/pyproject.toml \
        || { echo "pyproject.toml no longer bans importing $banned. The ban keeps app/log.py and app/telemetry.py the only writers of what leaves the process." >&2; exit 1; }
done
need_grep '^DB_TEST_SUITE = tests/integration$' Makefile
need backend/tests/tiers.py
need backend/tests/conftest.py
need_grep 'not in this run' backend/tests/conftest.py "The default run must say which tiers it left out. Without that line, a green make test hides a whole folder that never ran."
python3 - <<'PY' || fail "the skip scan did not pass; see above"
import sys

sys.path.insert(0, "backend")
try:
    from tests.test_gate import ROOT, switched_off
except ImportError as error:
    print(f"check: the skip scan could not run, which is not the same as passing. "
          f"test_gate.py must import no third-party package at module level, because the host python3 reads it: {error}",
          file=sys.stderr)
    sys.exit(1)

found = switched_off()
for path, marker in found:
    print(f"{path.relative_to(ROOT)} uses {marker!r}, and a skipped test exits 0 like a "
          f"passing one -- move it into a tier instead", file=sys.stderr)
sys.exit(1 if found else 0)
PY
TEST_FOLDERS="audit_events deployment devtools environment errors identity integration lifespan
              log main models observe routes serve store telemetry wiring"
need_only d backend/tests $TEST_FOLDERS
for folder in $TEST_FOLDERS; do
    need "backend/tests/$folder/__init__.py"
done
need_grep '^SHELL := /bin/bash' Makefile "The db-test recipe traps INT to stop its container. dash runs that handler only after the foreground command returns, which on Ctrl-C it never does."
need .github/workflows/ci.yml "test_workflow.py walks the workflows it finds, so it passes over a directory that is gone"
need backend/tests/test_workflow.py
need_grep 'make gate' .github/workflows/ci.yml "The workflow runs the list the hook runs, so the two cannot drift."
need_no_grep 'make pre-commit' .github/workflows/ci.yml "make pre-commit skips a half that is not installed, and the workflow must run both."
need_no_grep '\.jinja' .github/workflows/ci.yml
sed 's/#.*//' .github/workflows/ci.yml | grep -q 'make db-test' \
    || fail "ci.yml no longer runs 'make db-test' outside a comment, so tenant isolation is checked against a real Postgres nowhere after a push."

echo "==> assert the gate's own checks fail on what they are for"
python3 - <<'PY' || fail "a check in test_gate.py or test_workflow.py did not refuse what it is written to refuse. A text check can pass on a string that means nothing, so each one must fail on the tree it exists to catch."
import pathlib
import shutil
import sys

sys.path.insert(0, "backend")
try:
    from tests import test_gate, test_workflow
except ImportError as error:
    print(f"check: test_gate.py or test_workflow.py could not be imported, which is not the same as passing: {error}",
          file=sys.stderr)
    sys.exit(1)

failures = []


def refused(name, module=test_gate):
    try:
        getattr(module, name)()
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


for module, name in ((test_gate, "test_every_tier_is_selected_by_a_file_that_still_names_it"),
                     (test_gate, "test_every_tier_still_holds_tests"),
                     (test_workflow, "test_a_workflow_runs_the_gate_rather_than_a_copy_of_it")):
    if refused(name, module):
        failures.append(f"{name} fails on the tree as rendered, before any mutation")

mutation("test_every_tier_is_selected_by_a_file_that_still_names_it",
         "frontend/playwright.config.ts", 'testDir: "./e2e"', 'testDir: "./specs-e2e"',
         "a Playwright config pointed at a directory holding none of the tier's specs")

mutation("test_every_tier_is_selected_by_a_file_that_still_names_it",
         "Makefile", "DB_TEST_SUITE = tests/integration", "DB_TEST_SUITE = tests/db",
         "a Makefile that selects a folder the tier does not live in")

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
    if not refused("test_a_workflow_runs_the_gate_rather_than_a_copy_of_it", test_workflow):
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

if (cd frontend && node devtools/comments.mjs src --exclude src/nowhere) >/dev/null 2>&1; then
    fail "comments.mjs accepted an exclude naming a path that is not in the tree. That exclude matches nothing today and exempts whatever lands there tomorrow."
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
mkdir -p "$WORK/commented"
printf '#!/bin/sh\necho hi  # a note\n' >"$WORK/commented/hook"
printf 'all:\n\t@true\n# a note\n' >"$WORK/commented/Makefile"
for planted in hook Makefile; do
    if (cd backend && python3 devtools/comments.py "$WORK/commented/$planted") >/dev/null 2>&1; then
        fail "comments.py passed a comment in a $planted. A script is source, and the ban covers it."
    fi
done
rm -rf "$WORK/commented"

need_no_grep 'exec pnpm' devtools/dev.sh "Exec replaces the shell and drops its cleanup trap, which strands the backend after vite stops."
need_grep 'trap cleanup' devtools/dev.sh
need_grep 'set -m' devtools/dev.sh

for stopper in devtools/dev.sh devtools/contract-test.sh; do
    need_grep 'own_group' "$stopper"
    need_no_grep 'kill .*-- -' "$stopper"
    need_no_grep '|| kill' "$stopper"
done
need_grep 'BACKEND_PORT' devtools/dev.sh "Each port comes from the environment, so two checkouts can run at once."
need_grep 'FRONTEND_PORT' devtools/dev.sh
need_grep 'BACKEND_PORT' devtools/contract-test.sh
need_grep 'process.env.BACKEND_PORT' frontend/vite.config.ts
need_grep 'process.env.PREVIEW_PORT' frontend/playwright.config.ts "No gate runs the Playwright configs, so a hard-coded port fails nowhere."
need_grep 'process.env.FRONTEND_PORT' frontend/playwright.live.config.ts
need frontend/e2e/signed-in.ts
need_no_grep 'localhost:5173' frontend/playwright.live.config.ts "The live config starts no server, so a hard-coded port points at whatever else listens there."
need_grep 'reuseExistingServer: false' frontend/playwright.config.ts "Playwright checks only that something answers on the URL, so a stranger's server absorbs the suite."
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
    print("check: empty on purpose (docs/adr/template/0001), so the spec must create what it",
          file=sys.stderr)
    print("check: asserts rather than assume a fixture this half never declares.",
          file=sys.stderr)
    raise SystemExit(1)
PY
need_grep 'Read AGENTS.md' frontend/e2e/tasks.spec.ts "The mock spec asserts a row that src/mocks/store.ts declares, in its own half."
need_no_grep 'CONTRACT_BASE_URL ?=' Makefile "A default there overrides the port the script started the dev server on."

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
need_grep 'strip-mock-worker' frontend/vite.config.ts "The worker is a public/ asset, and only this plugin keeps it out of a production build."
need_grep 'BASE_URL}mockServiceWorker.js' frontend/src/main.tsx "A hard-coded leading slash breaks a deploy under a subpath."
need_grep 'basepath: import.meta.env.BASE_URL' frontend/src/router.tsx "A hard-coded leading slash breaks a deploy under a subpath."
need_grep 'import.meta.env.BASE_URL' frontend/src/api/base.ts "A hard-coded leading slash breaks a deploy under a subpath."

need backend/pyproject.toml
need backend/app/main.py
need backend/app/models/__init__.py
need backend/app/models/shared.py
need_absent backend/app/models.py
need backend/tests/models/test_layering.py "It holds the layering that docs/adr/template/0007 writes down"
need backend/app/routes/public.py
need backend/app/routes/tenant/__init__.py
need_absent backend/app/routes.py
need backend/app/errors.py
need backend/tests/errors/test_errors.py "It holds the rule that every refusal is a class (docs/adr/template/0006)"
need backend/app/deps.py
need backend/app/wiring.py
need backend/app/environment.py
need backend/app/request_line.py
need backend/app/log_lines.py
need backend/app/deployment.py
need backend/app/refusal.py
need backend/tests/tier.py
need backend/tests/models/layers.py
need backend/app/lifespan.py
need_grep 'refuse_development_settings' backend/app/lifespan.py "Unset APP_ENV is production, and production refuses the in-memory substrate (docs/adr/template/0010)."
need_grep 'APP_ENV=development' devtools/dev.sh
need_grep 'APP_ENV=development' devtools/contract-test.sh
need_grep 'PYTHONASYNCIODEBUG=1' devtools/dev.sh "Without asyncio debug mode, a step that holds the event loop logs nothing."
need_grep 'APP_ENV=development PYTHONASYNCIODEBUG=1 uv run uvicorn' Makefile "Without asyncio debug mode, a step that holds the event loop logs nothing."
need backend/tests/routes/test_tasks.py
need backend/app/serve.py "It is the one-origin entrypoint, for a deployment with no proxy to strip the /api prefix"
need backend/tests/serve/test_serve.py
need backend/app/store/__init__.py
need backend/app/store/memory.py "Two substrates stand behind one contract (docs/adr/template/0001)"
need backend/app/store/pg.py "Two substrates stand behind one contract (docs/adr/template/0001)"
need backend/app/store/ddl.py
need backend/app/store/migrate.py
need backend/app/store/conn.py
need backend/tests/store_contract.py
need backend/tests/store/test_store_contract.py "Without this runner, one substrate is checked and the other is only claimed"
need backend/tests/integration/test_store_contract.py "Without this runner, one substrate is checked and the other is only claimed"
need backend/tests/store/test_schema.py
need backend/tests/integration/test_postgres.py
need backend/tests/integration/test_isolation.py
need backend/tests/integration/conftest.py
need_absent backend/app/store.py
need deploy/schema.sql
need deploy/roles.sql
need backend/.schema-baseline.json "Without it, make schema re-records an edited body, and the gate stops refusing an edited shipped entry"
need deploy/compose.yaml
need_exec deploy/credentials.sh
need CONTEXT.md
need_only f docs/adr/template \
    0001-two-substrates-behind-one-contract.md \
    0002-tenant-isolation-is-forced-and-always-on.md \
    0003-the-application-never-applies-ddl.md \
    0004-a-route-cannot-escape-the-identity-seam.md \
    0005-routes-are-split-by-what-a-caller-presents.md \
    0006-a-refusal-is-a-class-declared-once.md \
    0007-the-models-are-a-layering-written-down.md \
    0008-the-spec-describes-what-the-service-actually-does.md \
    0009-the-one-origin-entrypoint-is-the-edge.md \
    0010-the-environment-is-read-in-one-place-and-run-in-another.md \
    0011-every-log-line-is-declared-and-written-as-json-to-stdout.md \
    0012-traces-and-metrics-leave-over-otlp-to-a-collector-the-deployment-owns.md \
    0013-a-test-never-decides-whether-to-run.md \
    0014-the-gate-runs-once-per-tree-and-one-at-a-time.md \
    0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md \
    0016-the-template-s-decisions-are-numbered-apart-from-yours.md \
    0017-every-store-operation-on-tenant-rows-records-an-audit-event.md

echo "==> assert tenant isolation is wired, not just described"
need_grep 'FORCE ROW LEVEL SECURITY' deploy/schema.sql "Under ENABLE alone, a table's owner bypasses its own policies and reads every row."
need_grep 'ENABLE ROW LEVEL SECURITY' deploy/schema.sql
need_grep 'WITH CHECK' deploy/schema.sql "Under USING alone, a tenant can insert a row it cannot then read."
need_grep "CHECK (tenant_id <> '')" deploy/schema.sql "Without it, a row with an empty tenant is readable by every connection that never set one."
need_grep 'ON tasks (tenant_id,' deploy/schema.sql "The policy cannot use an index that does not lead with tenant_id."
need_grep 'DATABASE_OWNER_URL' backend/app/migrate.py
need_grep 'OwnerCredentialVisible' backend/app/wiring.py
need_no_grep 'DATABASE_OWNER_URL' backend/app/store/pg.py "The application must not hold a credential that can apply DDL."
need_grep 'condition: service_healthy' deploy/compose.yaml "The migrate service must wait for a database that is up."
need_grep 'service_completed_successfully' deploy/compose.yaml
need_grep 'pg_isready -h 127.0.0.1' deploy/compose.yaml "Init runs on a server that answers on the unix socket only, so a socket probe reports healthy before init ends."
need_no_grep 'pg_isready -q' Makefile "Init runs on a server that answers on the unix socket only, so a socket probe reports healthy before init ends."

need_grep '"127.0.0.1:\${DB_PORT:-5433}:5432"' deploy/compose.yaml
awk '/^ *#/ { next }
     /^ *ports:/ { if ($0 !~ /ports: *$/) exit 1; inside = 1; next }
     inside && /^ *- / { if ($0 !~ /^ *- "127\.0\.0\.1:/) exit 1; next }
     { inside = 0 }' deploy/compose.yaml \
    || fail "deploy/compose.yaml publishes a port beyond 127.0.0.1"
need_no_grep 'network_mode' deploy/compose.yaml
awk '{ cmd = joined ? cmd " " $0 : $0; joined = /\\$/ }
     joined { next }
     { n = split(cmd, f, /[ \t]+/); on = 0
       for (i = 2; i <= n; i++) {
         if (f[i] == "run" && (f[i - 1] == "docker" || f[i - 1] == "container")) { on = 1; continue }
         if (!on) continue
         if (f[i] ~ /^--publish-all/ || f[i] ~ /^-[a-zA-Z]*P/) exit 1
         if (f[i] ~ /^(-[a-zA-Z]*p|--publish)$/ && f[i + 1] !~ /^127\.0\.0\.1:/) exit 1
         if (f[i] ~ /^(-[a-zA-Z]*p|--publish=)./ && f[i] !~ /^(-[a-zA-Z]*p|--publish=)127\.0\.0\.1:/) exit 1
         if (f[i] ~ /;$/ || f[i] ~ /^(&&|\|\||\|)$/) on = 0 } }' Makefile \
    || fail "a docker run in the Makefile publishes a port beyond 127.0.0.1"
need_no_grep 'localhost:\$(' Makefile

need backend/devtools/schema.py
need_grep 'CREATE TABLE IF NOT EXISTS tasks' deploy/schema.sql
need_grep 'applied_once' deploy/schema.sql
need_grep 'server_settings' backend/app/store/pg.py "The search path is a startup parameter. asyncpg clears a session SET on release, so a SET search_path holds for one checkout only."
need backend/devtools/export_openapi.py
need_grep '^3.12' backend/.python-version
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
split = [n for n in schemas if n.endswith('-Input') or n.endswith('-Output')]
assert not split, f'input/output schemas split: {split}. The aliases in src/api/types.ts import each schema by name.'
paths = spec['paths']
assert sorted(paths) == ['/client-events', '/tasks', '/tasks/{id}'], sorted(paths)
assert '201' in paths['/tasks']['post']['responses']
assert schemas['ClientEvent']['additionalProperties'] is False, 'the public route takes untrusted input, so ClientEvent must refuse an undeclared field'
assert schemas['ClientEvents']['additionalProperties'] is False, 'the public route takes untrusted input, so ClientEvents must refuse an undeclared field'
assert '204' in paths['/tasks/{id}']['delete']['responses']
for method in ('patch', 'delete'):
    assert 'content' in paths['/tasks/{id}'][method]['responses']['404'], f'{method} 404 declares no body. HTTPException returns one, and the typed handlers cannot mock a response with no model.'
"
need_grep '"/tasks"' openapi.json "The proxy strips the /api prefix, so the spec must not carry it."
need_grep 'rewrite' frontend/vite.config.ts
need_grep 'redirect_slashes = False' backend/app/main.py
need_grep 'separate_input_output_schemas=False' backend/app/main.py
need_grep 'pnpm dlx openapi-typescript@' frontend/package.json "The generator needs a TypeScript of its own (docs/constraints.md)."
need frontend/tests/api/contract.test.ts
need_grep 'CONTRACT_TARGET' frontend/tests/api/target.ts
need_grep 'againstLiveBackend' frontend/tests/api/contract.test.ts
need_grep 'CONTRACT_TARGET' frontend/tests/setup.ts "A mock worker in the live run intercepts the requests that run exists to make."
need_grep 'make -s pre-commit' .githooks/pre-commit
need_grep 'make -s gate' devtools/gate.sh
need_grep 'lock="/tmp/smoke-test-pre-commit-' devtools/gate.sh "The lock is per project, so two projects on one machine do not queue behind each other."
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
need frontend/devtools/file-length.mjs
need frontend/tests/devtools/file-length.test.ts
need_grep 'devtools/complexity.mjs' frontend/package.json
need_grep 'devtools/file-length.mjs' frontend/package.json
need_grep 'devtools/conformance.mjs' frontend/package.json
need_grep '"strict": true' frontend/tsconfig.json
need_grep 'noUncheckedIndexedAccess' frontend/tsconfig.json
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
need frontend/devtools/comments.mjs
need backend/devtools/comments.py
need frontend/tests/devtools/comments.test.ts "The table holds the comment scanner to both directions"
need frontend/devtools/scan.mjs
need frontend/devtools/gate.mjs
need frontend/tests/devtools/conformance.test.ts
need_grep 'startsARegex' frontend/devtools/scan.mjs
need_no_grep 'const REGIONS' frontend/devtools/comments.mjs "Both gates read devtools/scan.mjs. A copy drifts from the scanner the tables test."
need_no_grep 'const REGIONS' frontend/devtools/conformance.mjs "Both gates read devtools/scan.mjs. A copy drifts from the scanner the tables test."
need_grep 'devtools/comments.mjs' frontend/package.json
need_grep 'devtools/comments.py' backend/devtools/lint.py
need backend/tests/devtools/test_comments.py "The table is the only witness that the Python gate reads a hash inside a string as data"
(cd frontend && node devtools/comments.mjs src tests e2e devtools) \
    || fail "the rendered frontend carries a comment"
(cd backend && python3 devtools/comments.py app tests devtools) \
    || fail "the rendered backend carries a comment"
need_grep '"--tree"' backend/devtools/lint.py "make lint is what holds the project's scripts to the comment ban."
python3 backend/devtools/comments.py --tree "$REPO" \
    || fail "a script in this repository or in the template carries a comment. AGENTS.md holds both to the rules the template ships."

need_grep 'max-complexity' backend/pyproject.toml
need_grep 'max-statements' backend/pyproject.toml
need_grep '\[tool.complexity\]' backend/pyproject.toml
for family in SIM RET PIE C4 PERF ERA C90 PLR0915 TID251; do
    grep -q "\"$family\"" backend/pyproject.toml || { echo "ruff family $family missing" >&2; exit 1; }
done

echo "==> assert the conformance checks"
CONF="$WORK/conformance"
CONFORMANCE="$OUT/frontend/devtools/conformance.mjs"
mkdir -p "$CONF/bad" "$CONF/good"

: >"$CONF/theme.css"
conformance() { node "$CONFORMANCE" --theme-file "$CONF/theme.css" "$@"; }

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
        echo "conformance check $rule did not fire. A rule that stops matching looks exactly like a clean codebase." >&2
        printf '%s\n' "$conformance_output" >&2
        exit 1
    fi
done

if ! printf '%s\n' "$conformance_output" | grep -q 'commented.tsx:.*effect-data'; then
    echo "conformance lost an effect body to a comment. bad.tsx trips effect-data with no comment in it, so only commented.tsx shows this." >&2
    printf '%s\n' "$conformance_output" >&2
    exit 1
fi

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
        echo "conformance let an arbitrary value through: $expected. The table of rules above stays green while one value passes." >&2
        printf '%s\n' "$conformance_output" >&2
        exit 1
    fi
done

if ! conformance "$CONF/good" >/dev/null 2>&1; then
    echo "conformance wrongly flagged legitimate code:" >&2
    conformance "$CONF/good" >&2 || true
    exit 1
fi

printf 'export const brand = "#5865f2";\n' >"$CONF/good/brand.ts"
if conformance "$CONF/good" >/dev/null 2>&1; then
    echo "conformance missed a raw colour outside the theme" >&2
    exit 1
fi
if ! conformance "$CONF/good" --allow '#5865f2' >/dev/null 2>&1; then
    echo "conformance ignored an allowlisted value. Without the allowlist, the only way past a false positive is to delete the check." >&2
    exit 1
fi

need_grep 'icon-stroke: 1.5' frontend/src/index.css "A rule that reads a token nobody declared resolves to an empty value."
need_grep '\.lucide' frontend/src/index.css "A token with no rule to carry it to the icons themes nothing."
need_grep 'stroke-width: var(--icon-stroke)' frontend/src/index.css

echo "==> assert the supply-chain policy"
need_grep 'minimumReleaseAge: 10080' frontend/pnpm-workspace.yaml
need_grep 'dlxCacheMaxAge' frontend/pnpm-workspace.yaml
need_grep 'openapi-typescript@7\.13\.0' frontend/package.json "The dlx cache is held for a month, which is safe only while every dlx call names an exact version."
need_grep 'trustPolicy: no-downgrade' frontend/pnpm-workspace.yaml
need_grep 'semver@6.3.1' frontend/pnpm-workspace.yaml
need_grep 'exclude-newer = "7 days"' backend/pyproject.toml
python3 - <<'PY'
import re

text = open("frontend/pnpm-workspace.yaml", encoding="utf-8").read()
for key in ("trustPolicyExclude", "minimumReleaseAgeExclude"):
    block = re.search(rf"^{key}:\n((?:[ \t]*-[ \t].*\n)+)", text, re.M)
    if block is None:
        continue
    for line in block.group(1).splitlines():
        entry = line.strip().lstrip("-").strip().strip("\"'")
        assert "@" in entry.lstrip("@"), f"{key} entry is not pinned to a version: {entry}. A bare name exempts every future release of it too."
PY
python3 - <<'PY'
import tomllib

deps = tomllib.load(open("backend/pyproject.toml", "rb"))["project"]["dependencies"]
unbounded = [dep for dep in deps if "<" not in dep]
assert not unbounded, f"runtime dependencies with no upper bound: {unbounded}. fastapi and pydantic write part of openapi.json, so a later release changes a committed artifact."
PY
need_grep 'required-version = ">=0.11.25"' backend/pyproject.toml 'An older uv rejects "7 days" with a date-parsing error that names neither uv nor the version.'
need_grep 'UV_EXCLUDE_NEWER' Makefile
need_absent frontend/pnpm-lock.yaml
need_absent backend/uv.lock
need_grep 'pnpm-lock.yaml' docs/installation.md "No lockfile ships, so the setup instructions must say to commit the ones make install writes."

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

    printf '#!/bin/sh\necho SECOND-PRE\n' >.git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
    sh devtools/install-hooks.sh >/dev/null
    need_exec .git/hooks/pre-commit.local
    need_exec .git/hooks/pre-commit.local.2

    sh devtools/install-hooks.sh >/dev/null
    need_absent .git/hooks/pre-commit.local.3 "Re-arming with nothing new installed must not grow the chain."

    hooks_abs="$(cd .git/hooks && pwd -P)"
    ln -s "$hooks_abs" ../hooks-link
    for equivalent in "$hooks_abs" ".git/hooks" "../hooks-link"; do
        git config core.hooksPath "$equivalent"
        if ! sh devtools/install-hooks.sh >/dev/null 2>&1; then
            echo "install-hooks.sh rejected an equivalent hooksPath: $equivalent. A symlink or a relative path to the hooks directory is that directory, as /var is /private/var on macOS." >&2
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
if [ "$VARIANT" = "default" ]; then
    run "test_gate.py without the workflow" sh -c \
        "cd '$NO_CI/backend' && '$OUT/backend/.venv/bin/python' -m pytest -q -p no:cacheprovider tests/test_gate.py"
fi

echo "==> assert basedpyright checks a call a test makes through the test client's portal"
cat >backend/tests/test_portal_is_typed.py <<'PY'
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def needs(app: FastAPI) -> FastAPI:
    return app


def calls(client: TestClient) -> None:
    assert client.portal is not None
    client.portal.call(needs, 0)
PY
out="$(cd backend && uv run --no-sync basedpyright tests/test_portal_is_typed.py 2>&1)" && typed=no || typed=yes
rm backend/tests/test_portal_is_typed.py
[ "$typed" = yes ] || fail "basedpyright accepted an int where a FastAPI is expected: it sees TestClient.portal as Unknown"
printf '%s\n' "$out" | grep -q 'reportCallIssue' || {
    echo "basedpyright failed, but not on the call through the portal:" >&2
    printf '%s\n' "$out" >&2
    exit 1
}

echo "==> frontend: install, lint, test, build"
run "pnpm install" pnpm -C frontend install --prefer-offline

echo "==> assert a fresh install ships no known high-severity vulnerability"
pnpm -C frontend audit --prod --json >"$WORK/audit.json" || true
python3 - "$WORK/audit.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        report = json.load(handle)
except (OSError, ValueError):
    print("check: pnpm audit returned no report; skipping (registry unreachable?). An unreachable registry is not a vulnerability.")
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
bundles_msw() { grep -rl 'setupWorker' frontend/dist/assets/*.js >/dev/null 2>&1; }
run "vite build --mode mock" pnpm -C frontend build:mock
need frontend/dist/mockServiceWorker.js
bundles_msw || fail "the mock build no longer bundles msw; its e2e specs cannot pass. This script runs no e2e suite, so nothing else sees it."

run "vite build" pnpm -C frontend build

echo "==> assert the build output"
need frontend/dist/index.html
ls frontend/dist/assets/index-*.js >/dev/null
ls frontend/dist/assets/index-*.css >/dev/null
! bundles_msw || fail "msw leaked into the production bundle. In production, the worker would answer the user's requests."
need_absent frontend/dist/mockServiceWorker.js "public/ is copied into every build, and only vite.config.ts deletes this file again"

echo "==> the secret scan passes the project and refuses a planted token"
run "secret scan" sh devtools/secrets.sh
PLANTED="planted-$$.txt"
printf 'token = ghp_%s\n' "$(python3 -c 'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36)))')" >"$PLANTED"
if sh devtools/secrets.sh >/dev/null 2>&1; then
    rm -f "$PLANTED"
    fail "devtools/secrets.sh passed a tree holding a GitHub token. A scan that never fires passes forever."
fi
rm -f "$PLANTED"

echo "==> assert the contract artifacts are in sync with the code"
run "make openapi" make openapi
git diff --exit-code -- openapi.json frontend/src/api/schema.ts \
    || { echo "the committed contract artifacts do not match the rendered code" >&2; exit 1; }

echo "==> the contract suite, against both implementations"
run "contract suite (mocks)" pnpm -C frontend test:contract
run "contract suite (live backend)" env -u BACKEND_PORT -u FRONTEND_PORT make test-contract
need_nothing_outlived "the contract suite"

echo "==> the store, against a real Postgres"
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
python3 - "$OUT" <<'PY'
import os
import pty
import signal
import subprocess
import sys
import time

BACKEND_PORT = os.environ["BACKEND_PORT"]
FRONTEND_PORT = os.environ["FRONTEND_PORT"]
BACKEND = f"http://localhost:{BACKEND_PORT}/tasks"
PROXY = f"http://localhost:{FRONTEND_PORT}/api/tasks"


def serving(url):
    return subprocess.run(
        ["curl", "-fsS", "--max-time", "2", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


PORTS = ((f":{BACKEND_PORT}", BACKEND), (f":{FRONTEND_PORT}", PROXY))
already = " and ".join(n for n, u in PORTS if serving(u))
if already:
    print(f"check: {already} already in use before dev.sh started -- something else is\n"
          f"       serving it (another `make dev`?). Stop it and run this again.\n"
          f"       Otherwise the probes read that process, and this check blames dev.sh.",
          file=sys.stderr)
    raise SystemExit(1)

pid, master = pty.fork()
if pid == 0:
    os.chdir(sys.argv[1])
    os.execvp("sh", ["sh", "devtools/dev.sh"])

os.set_blocking(master, False)
output = bytearray()


def drain():
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


if not until(lambda: serving(PROXY), 120):
    die(f"dev.sh did not serve :{FRONTEND_PORT}/api within 120s")

os.write(master, b"\x03")

if not until(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid, 30):
    die("dev.sh was still running 30s after Ctrl-C")

if not until(lambda: not serving(BACKEND) and not serving(PROXY), 15):
    held = " and ".join(n for n, u in PORTS if serving(u))
    die(f"dev.sh exited but {held} still answering")
PY
need_nothing_outlived "the dev loop"

echo "==> the built bundle, answered by app.serve on one origin"
ONE_ORIGIN="http://localhost:$BACKEND_PORT"
PROBE="$ONE_ORIGIN/api/tasks"

if curl -fsS --max-time 2 "$PROBE" >/dev/null 2>&1; then
    fail ":$BACKEND_PORT is answering before app.serve started -- something else has it"
fi

env -u DATABASE_URL APP_ENV=development FRONTEND_BUNDLE="$OUT/frontend/dist" \
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

shell="$(curl -fsS --max-time 10 "$ONE_ORIGIN/")" \
    || fail "app.serve did not answer / at all"
printf '%s' "$shell" | grep -q '<div id="root">' \
    || fail "app.serve answered / with something that is not the built shell"

deep="$(curl -fsS --max-time 10 "$ONE_ORIGIN/some/client/route")" \
    || fail "app.serve refused a client route instead of falling back to the shell"
[ "$deep" = "$shell" ] || fail "a deep link did not fall back to the built index.html. A reload on a client route must reach the same document on both edges."

cache_control_of() {
    headers="$(curl -fsS --max-time 10 -o /dev/null -D - "$1")" || return 1
    printf '%s\n' "$headers" | tr -d '\r' | sed -n 's/^[Cc]ache-[Cc]ontrol: //p'
}
for page in / /some/client/route; do
    page_cache="$(cache_control_of "$ONE_ORIGIN$page")" \
        || fail "app.serve did not answer $page for its cache-control"
    [ "$page_cache" = "no-cache" ] \
        || fail "app.serve answered $page with cache-control '${page_cache}', not no-cache"
done

refs="$(printf '%s' "$shell" | grep -oE '(src|href)="[^"]*\.(js|css)"' \
            | sed 's/^[a-z]*="//; s/"$//')"
[ -n "$refs" ] || fail "the built index.html names no script or stylesheet to load. A loop over nothing reports success."

for ref in $refs; do
    case "$ref" in
        *.js)  want=javascript ;;
        *.css) want=css ;;
        *)     continue ;;
    esac
    ref_type="$(curl -s --max-time 10 -o /dev/null -w '%{content_type}' "$ONE_ORIGIN$ref")"
    case "$ref_type" in
        *"$want"*) ;;
        *) fail "the shell asks for $ref and app.serve answered it as ${ref_type:-nothing}. The fallback answers a missed asset with the shell and a 200, so only the type shows the miss." ;;
    esac
    ref_cache="$(cache_control_of "$ONE_ORIGIN$ref")" \
        || fail "app.serve did not answer $ref for its cache-control"
    [ "$ref_cache" = "public, max-age=31536000, immutable" ] \
        || fail "app.serve answered $ref with cache-control '${ref_cache}', not immutable"
done

stale_status="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' \
    "$ONE_ORIGIN/assets/index-deadbee.js")"
[ "$stale_status" = "404" ] \
    || fail "a hashed asset the bundle lacks answered $stale_status, not 404. The shell in its place fails as a syntax error far from the deploy that caused it."

run "contract suite (app.serve)" env CONTRACT_TARGET=live VITE_API_BASE_URL="$ONE_ORIGIN/api" \
    pnpm -C frontend test:contract

kill "$serve_pid" 2>/dev/null || true
wait "$serve_pid" 2>/dev/null || true
serve_pid=""

echo "==> assert the drift gate is two-sided, on both halves"
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

    seed='const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8"));
    const density = Number((measured.density * Number(process.argv[1])).toFixed(3));
    const sum = Math.round(measured.sum * Number(process.argv[2]));
    fs.writeFileSync("baseline.json", JSON.stringify({ ...measured, density, sum, origin: density }, null, 2));'

    settled='const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8"));
    const tree = process.argv[3] === "tree";
    const want = {
        density: tree ? measured.density : Number((measured.density * Number(process.argv[1])).toFixed(3)),
        sum: tree ? measured.sum : Math.round(measured.sum * Number(process.argv[2])),
    };
    const got = JSON.parse(fs.readFileSync("baseline.json", "utf8"));
    for (const key of ["density", "sum"]) {
        if (got[key] === want[key]) continue;
        console.error(`baseline holds ${key} ${got[key]}, wanted ${want[key]}`);
        process.exit(1);
    }'

    while IFS='|' read -r factor sum_factor flags expect after; do
        [ -n "$factor" ] || continue
        node -e "$seed" "$factor" "$sum_factor"
        rc=0
        node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json $flags \
            >/dev/null 2>&1 || rc=1
        if [ "$rc" != "$expect" ]; then
            echo "frontend drift: ${factor}x/${sum_factor}x with '$flags' exited $rc, wanted $expect" >&2
            exit 1
        fi
        if ! node -e "$settled" "$factor" "$sum_factor" "$after"; then
            echo "frontend drift: ${factor}x/${sum_factor}x with '$flags' should have left '$after'" >&2
            exit 1
        fi
    done <<'DRIFT_CASES'
0.99|0.99|--tighten-baseline|0|same
1.01|1.01|--tighten-baseline|0|same
1.05|1.05|--tighten-baseline|0|tree
1.05|1.05||1|same
0.90|0.90||1|same
0.90|0.90|--tighten-baseline|1|same
0.90|1.00|--tighten-baseline|0|tree
0.90|1.00||1|same
0.90|1.10|--tighten-baseline|0|tree
0.90|1.10||1|same
1.05|1.00|--tighten-baseline|0|tree
1.05|1.00||1|same
1.05|0.90|--tighten-baseline|0|tree
DRIFT_CASES

    node -e "$seed" 1.05 1.05
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json 2>&1 \
        | grep -q 'baseline is .* above the tree'
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline tightened'

    node -e "$seed" 0.90 1.00
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json 2>&1 \
        | grep -q 'complexity sum did not move with the density'
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline re-recorded'

    node -e "$seed" 1.05 1.00
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline re-recorded'

    node -e "$seed" 1.05 1.05
    node -e '
    const fs = require("node:fs");
    const { sum, ...rest } = JSON.parse(fs.readFileSync("baseline.json", "utf8"));
    fs.writeFileSync("baseline.json", JSON.stringify(rest, null, 2));'
    refusal="$(node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json 2>&1)" \
        && { echo "a baseline carrying no complexity sum was accepted" >&2; exit 1; }
    printf '%s\n' "$refusal" | grep -q 'carries no complexity sum' || {
        echo "the gate refused, but not on the missing complexity sum:" >&2
        printf '%s\n' "$refusal" >&2
        exit 1
    }

    node -e "$seed" 1.05 1.05
    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline >/dev/null

    node -e '
    const fs = require("node:fs");
    const measured = JSON.parse(fs.readFileSync("measured.json", "utf8")).density;
    const after = JSON.parse(fs.readFileSync("baseline.json", "utf8"));
    if (after.density !== measured) throw new Error("tightening did not lower the density");
    if (after.origin === measured) throw new Error("tightening moved the ceiling anchor. A tree that improves and then regresses would walk past the ceiling.");
    '
)

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

    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json >/dev/null
    node -e '
    const density = JSON.parse(require("node:fs").readFileSync("baseline.json", "utf8")).density;
    if (density === 0) throw new Error("the checking half rewrote the baseline");
    '

    node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        --tighten-baseline | grep -q 'baseline tightened'
    node -e '
    const after = JSON.parse(require("node:fs").readFileSync("baseline.json", "utf8"));
    if (after.density !== 0) throw new Error("an improvement to zero was not recorded. Zero returns before the drift check, so its slack is the whole baseline.");
    if (!(after.origin > 0)) throw new Error("tightening to zero moved the ceiling anchor");
    '

    node -e "$branching"
    if node "$OUT/frontend/devtools/complexity.mjs" src --baseline baseline.json \
        >/dev/null 2>&1; then
        echo "complexity reintroduced after a fall to zero passed against a stale baseline" >&2
        exit 1
    fi
)

CX="$WORK/complexity"
mkdir -p "$CX"

cat >"$CX/fake-ruff" <<'FAKE_RUFF'
#!/bin/sh
echo '[{"message":"a is too complex (2 > 0)"},{"message":"b is too complex (2 > 0)"}]'
FAKE_RUFF
chmod +x "$CX/fake-ruff"

compare='import json,operator,sys
recorded = json.load(open(sys.argv[1]))["mean"]
sys.exit(not getattr(operator, sys.argv[3])(recorded, float(sys.argv[2])))'

backend_settled='import json,sys
recorded = json.load(open("baseline.json"))
tree = sys.argv[3] == "tree"
want = (2.0, 4.0) if tree else (float(sys.argv[1]), float(sys.argv[2]))
sys.exit(0 if (recorded["mean"], recorded["sum"]) == want else 1)'

while IFS='|' read -r before before_sum flags expect after; do
    [ -n "$before" ] || continue
    printf '{"callables": 2.0, "sum": %s, "mean": %s, "p90": 2.0}\n' \
        "$before_sum" "$before" >"$CX/baseline.json"
    rc=0
    (cd "$CX" && "$OUT/backend/.venv/bin/python" "$OUT/backend/devtools/complexity.py" . \
        --ruff "$CX/fake-ruff" --baseline baseline.json --min-callables 1 $flags) \
        >/dev/null 2>&1 || rc=1
    if [ "$rc" != "$expect" ]; then
        echo "backend drift: $before/$before_sum with '$flags' exited $rc, wanted $expect" >&2
        exit 1
    fi
    if ! (cd "$CX" && python3 -c "$backend_settled" "$before" "$before_sum" "$after"); then
        echo "backend drift: $before/$before_sum with '$flags' should have left '$after'" >&2
        exit 1
    fi
done <<'DRIFT_CASES'
1.970|4.0|--tighten-baseline|0|same
2.030|4.0|--tighten-baseline|0|same
1.900|3.0|--tighten-baseline|1|same
1.900|3.0||1|same
2.400|6.0||1|same
2.400|6.0|--tighten-baseline|0|tree
1.900|5.0|--tighten-baseline|0|tree
1.900|5.0||1|same
1.900|4.0|--tighten-baseline|0|tree
1.900|4.0||1|same
2.400|3.0|--tighten-baseline|0|tree
2.400|3.0||1|same
2.400|4.0|--tighten-baseline|0|tree
2.400|4.0||1|same
DRIFT_CASES

printf '{"callables": 2.0, "mean": 2.400, "p90": 2.0}\n' >"$CX/baseline.json"
recovery="$(cd "$CX" && "$OUT/backend/.venv/bin/python" "$OUT/backend/devtools/complexity.py" . \
    --ruff "$CX/fake-ruff" --baseline baseline.json --min-callables 1 --tighten-baseline)" \
    || fail "backend complexity refused a baseline recorded without a complexity sum"
printf '%s\n' "$recovery" | grep -q 'recovered 5 from mean x callables' \
    || fail "backend complexity recovered a missing complexity sum without saying so"
(cd "$CX" && python3 -c "$backend_settled" 2.400 5.0 tree) \
    || fail "backend complexity recovered a sum that changed what the drift meant"

printf '{"callables": 2.0, "sum": 5.0, "mean": 1.900, "p90": 2.0}\n' >"$CX/baseline.json"
(cd "$CX" && "$OUT/backend/.venv/bin/python" "$OUT/backend/devtools/complexity.py" . \
    --ruff "$CX/fake-ruff" --baseline baseline.json --min-callables 1) 2>&1 \
    | grep -q 'complexity sum did not move with the mean' \
    || fail "backend complexity reported an arithmetic drift as a rise"

echo "==> the tree fingerprint sees a same-size edit made in the instant after a write"
race="$(mktemp -d)"
(
    cd "$race"
    git init -q
    for _ in $(seq 1 300); do
        printf 'one\n' >kept.txt
        git add kept.txt
        before="$(sh "$OUT/devtools/worktree-tree.sh")"
        printf 'two\n' >kept.txt
        [ "$(sh "$OUT/devtools/worktree-tree.sh")" != "$before" ] || exit 1
    done
) || { rm -rf "$race"; fail "devtools/worktree-tree.sh kept the tree of a file edited in the same instant. Git trusts size and timestamps unless the index was written no earlier than the file."; }
rm -rf "$race"

echo "==> the gate runner skips a tree that already passed"
sh devtools/worktree-tree.sh >"$(git rev-parse --git-path gate-passed)"
out="$(sh devtools/gate.sh 2>&1)" || fail "devtools/gate.sh failed on a tree recorded as passed: $out"
printf '%s\n' "$out" | grep -q 'already passed the gate' \
    || fail "devtools/gate.sh ran again over a tree recorded as passed: $out"

echo "==> assert each half's fixing variant tightens and its checking variant refuses"
node -e '
const pkg = JSON.parse(require("node:fs").readFileSync("frontend/package.json", "utf8"));
if (!pkg.scripts.lint.includes("--tighten-baseline")) throw new Error("pnpm lint cannot tighten");
if (pkg.scripts["lint:check"].includes("--tighten-baseline")) throw new Error("lint:check tightens");
'

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
pnpm -C frontend run complexity | grep -q 'drift ok'

INFLATED="$(node -e '
const fs = require("node:fs");
const path = "frontend/.complexity-baseline.json";
const baseline = JSON.parse(fs.readFileSync(path, "utf8"));
const density = baseline.density * 1.05;
const sum = Math.round(baseline.sum * 1.05);
fs.writeFileSync(path, JSON.stringify({ ...baseline, density, sum }, null, 2));
process.stdout.write(String(density));
')"

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

mv backend/.complexity-baseline.json "$WORK/baseline.parked"
if (cd backend && uv run --no-sync python devtools/complexity.py app \
        --baseline .complexity-baseline.json >/dev/null 2>&1); then
    fail "backend complexity accepted a missing baseline above min-callables. A drift check that passes with no baseline never starts."
fi
mv "$WORK/baseline.parked" backend/.complexity-baseline.json

sed 's/^min-callables = 50$/min-callables = 1/' backend/pyproject.toml >"$WORK/pyproject.floored"
cp "$WORK/pyproject.floored" backend/pyproject.toml
need_grep '^min-callables = 1$' backend/pyproject.toml
python3 -c "
import json
path = 'backend/.complexity-baseline.json'
recorded = json.load(open(path))
json.dump({**recorded, 'mean': 2.9, 'sum': recorded['sum'] * 2}, open(path, 'w'), indent=2)
"

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
