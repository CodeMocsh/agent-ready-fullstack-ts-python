#!/bin/sh
# Render the template from the working tree and print where the project landed.
#
# This is the half of check_template.sh that is not an assertion. It exists on its
# own because everything in that script needs a rendered project and rendering one
# was previously reachable only by running all of it: a one-line change to a single
# check cost a full render, both installs, both test suites and a build. Now it
# costs this, once, and then as many runs of the check as the change takes.
#
#   sh devtools/render.sh                 -- render the default variant, print the path
#   sh devtools/render.sh proprietary     -- a license variant
#   sh devtools/render.sh --into DIR      -- work inside DIR rather than a fresh mktemp
#   sh devtools/render.sh -- --data k=v   -- everything after `--` reaches copier as is,
#                                            and replaces the default answer for that key
#   sh devtools/render.sh --spec          -- print the copier pin and exit
#
# It asserts one thing before printing: that nothing was left unrendered. That is a
# property of the render, not of the project, so it belongs here.
#
# The caller owns the directory this prints into and is responsible for removing it.
# Nothing here traps EXIT: a trap that cleaned up would delete the tree the moment
# this script returned, which is the one thing the caller wanted.
set -eu

# Git exports GIT_DIR and friends into the environment of the hooks it runs. This
# renders into temp directories and runs git inside them, so an inherited git
# context silently points that work at the calling repository.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

# The one pin. check_template.sh reads it back through `--spec` rather than keeping
# a second copy, because a check that guards eleven copies of a version must not be
# the twelfth.
COPIER_SPEC="copier@9.17.1"

if [ "${1:-}" = "--spec" ]; then
    echo "$COPIER_SPEC"
    exit 0
fi

export UV_EXCLUDE_NEWER="14 days"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

VARIANT=default
WORK=""

# Everything after `--` is handed to copier untouched, and it has to travel in "$@"
# rather than in a string: the answers the hostile-input check renders with carry
# spaces, quotes and backslashes on purpose, and a string would word-split them back
# into something nobody typed.
while [ $# -gt 0 ]; do
    case "$1" in
        --into)
            [ $# -ge 2 ] || { echo "render: --into needs a directory" >&2; exit 2; }
            WORK="$2"; shift 2 ;;
        --) shift; break ;;
        -*) echo "render: unknown option '$1'" >&2; exit 2 ;;
        *) VARIANT="$1"; shift ;;
    esac
done

# Defaults are added only for answers the caller did not name, rather than passed
# first and left for copier to overwrite. Whether a repeated `--data` takes the first
# value or the last is copier's business and undocumented either way; not repeating
# one means it never has to be.
supplied() {
    wanted="$1"
    shift
    for arg in "$@"; do
        case "$arg" in "$wanted"=*) return 0 ;; esac
    done
    return 1
}

for answer in \
    package_name=smoke-test \
    "package_description=A smoke test project" \
    "package_author_name=Test Author" \
    package_author_email=test@example.com \
    package_github_org=testorg; do
    supplied "${answer%%=*}" "$@" || set -- "$@" --data "$answer"
done

case "$VARIANT" in
    default)      LICENSE="" ;;
    proprietary)  LICENSE="--data package_license=Proprietary" ;;
    no-license)   LICENSE="--data package_license=None" ;;
    *) echo "render: unknown variant '$VARIANT' (default|proprietary|no-license)" >&2; exit 2 ;;
esac

for tool in uvx tar git; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "render: $tool is required and not on PATH." >&2
        echo "render: see template/docs/installation.md for how to install it." >&2
        exit 1
    fi
done

[ -n "$WORK" ] || WORK="$(mktemp -d)"
mkdir -p "$WORK"
SRC="$WORK/src"
OUT="$WORK/out"
mkdir -p "$SRC"

# Staged through a file rather than a pipe on purpose: in a pipeline only the last
# command's status reaches "set -e", so a failed archive step would still extract
# whatever it managed to write and the run would go on against a partial tree --
# passing checks on a template that is not the one on disk. "set -o pipefail" is the
# usual fix but is not in POSIX sh, and this script runs under dash wherever /bin/sh
# is, where that line is itself an error.
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

# shellcheck disable=SC2086
uvx --exclude-newer "14 days" "$COPIER_SPEC" copy --defaults --quiet --trust \
    --vcs-ref=HEAD \
    $LICENSE "$@" \
    "$SRC" "$OUT" >&2

# A render is not finished until nothing is left unrendered, so this travels with the
# render rather than sitting in the caller. The forgotten-suffix failure mode -- a
# token left in a file that was never given a .jinja suffix -- produces a project
# carrying a literal {{ package_name }}, and a `make render` that handed one back
# unchecked would be the loop where you develop against it without noticing.
#
# The [^$] excludes GitHub Actions' ${{ }}, which is not a Copier token.
if grep -rEn '(^|[^$])\{\{ *[a-z_]+ *\}\}' "$OUT" 2>/dev/null; then
    echo "render: unrendered token above: a file carrying tokens needs the .jinja suffix" >&2
    exit 1
fi
if grep -rn '{%' "$OUT" 2>/dev/null; then
    echo "render: unrendered Jinja statement above" >&2
    exit 1
fi
if [ -n "$(find "$OUT" -name '*.jinja' 2>/dev/null)" ]; then
    echo "render: a .jinja file survived: $(find "$OUT" -name '*.jinja')" >&2
    exit 1
fi

echo "$OUT"
