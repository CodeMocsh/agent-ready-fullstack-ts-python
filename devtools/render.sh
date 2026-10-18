#!/bin/sh
set -eu

unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

COPIER_SPEC="copier@9.17.1"

if [ "${1:-}" = "--spec" ]; then
    echo "$COPIER_SPEC"
    exit 0
fi

export UV_EXCLUDE_NEWER="7 days"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=maintenance.auto GIT_CONFIG_VALUE_0=false
REPO="$(cd "$(dirname "$0")/.." && pwd)"

VARIANT=default
WORK=""

while [ $# -gt 0 ]; do
    case "$1" in
        --into)
            [ $# -ge 2 ] || { echo "render: --into needs a directory" >&2; exit 2; }
            WORK="$2"; shift 2 ;;
        --) shift; break ;;
        -*) echo "render: unknown option '$1'. Usage: render.sh [--spec] [VARIANT] [--into DIR] [-- COPIER_ARGS]. A --data after -- replaces the default answer for its key." >&2; exit 2 ;;
        *) VARIANT="$1"; shift ;;
    esac
done

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

tar --exclude=.git --exclude=node_modules --exclude=.venv \
    -cf "$WORK/tree.tar" -C "$REPO" .
tar -xf "$WORK/tree.tar" -C "$SRC"
rm -f "$WORK/tree.tar"

git -C "$SRC" init -q -b main
git -C "$SRC" -c user.email=check@example.com -c user.name=check \
    -c commit.gpgsign=false add -A
git -C "$SRC" -c user.email=check@example.com -c user.name=check \
    -c commit.gpgsign=false commit -qm "working tree under test"

uvx --exclude-newer "7 days" "$COPIER_SPEC" copy --defaults --quiet --trust \
    --vcs-ref=HEAD \
    $LICENSE "$@" \
    "$SRC" "$OUT" >&2

if grep -rEn '(^|[^$])\{\{ *[a-z_]+ *\}\}' "$OUT" 2>/dev/null; then
    echo "render: unrendered token above. A file that carries a Copier token needs the .jinja suffix, or every project gets the token as literal text." >&2
    echo 'render: the match skips ${{ }}, because that is GitHub Actions syntax and not a Copier token.' >&2
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
