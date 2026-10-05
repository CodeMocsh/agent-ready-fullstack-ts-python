#!/bin/sh
# The tree the gate certifies, read by gitleaks for a secret about to be committed. It is the tree
# devtools/worktree-tree.sh names -- the working tree as `git add -A` would stage it -- so an
# unstaged file is read and an ignored one is not, and a pass the gate records covers what it says.
#
# gitleaks is fetched once, by version, and refused unless its archive matches the checksum
# devtools/gitleaks.sums copies from the release's own list. A finding is either removed and the
# secret rotated, or, where it is no secret at all, its fingerprint goes in .gitleaksignore.
#
#   sh devtools/secrets.sh          fetch if needed, then scan
#   sh devtools/secrets.sh fetch    fetch only, which is what `make install` does
#   sh devtools/secrets.sh tool     fetch if needed, then print where gitleaks is, for
#                                   another script to run the same pinned build
set -eu

case "${1:-}" in
    "" | fetch | tool) ;;
    *)
        echo "secrets: no such mode '$1'. Give nothing, fetch, or tool." >&2
        exit 1
        ;;
esac

version=8.30.1

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

case "$(uname -s)_$(uname -m)" in
    Darwin_arm64) platform=darwin_arm64 ;;
    Darwin_x86_64) platform=darwin_x64 ;;
    Linux_x86_64) platform=linux_x64 ;;
    Linux_aarch64) platform=linux_arm64 ;;
    *)
        echo "secrets: gitleaks publishes no build for $(uname -sm)" >&2
        exit 1
        ;;
esac

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

tool="$root/build/tools/gitleaks-$version/gitleaks"
if [ ! -x "$tool" ]; then
    archive="gitleaks_${version}_${platform}.tar.gz"
    sum="$(grep "  $archive\$" devtools/gitleaks.sums)" || {
        echo "secrets: devtools/gitleaks.sums has no checksum for $archive" >&2
        exit 1
    }
    curl -fsSL -o "$scratch/$archive" \
        "https://github.com/gitleaks/gitleaks/releases/download/v$version/$archive"
    if command -v sha256sum >/dev/null; then check="sha256sum -c -"; else check="shasum -a 256 -c -"; fi
    (cd "$scratch" && echo "$sum" | $check >/dev/null)
    tar -xzf "$scratch/$archive" -C "$scratch" gitleaks
    mkdir -p "$(dirname "$tool")"
    mv "$scratch/gitleaks" "$tool"
fi

[ "${1:-}" = fetch ] && exit 0
if [ "${1:-}" = tool ]; then
    echo "$tool"
    exit 0
fi

tree="$(sh devtools/worktree-tree.sh)"
git archive -o "$scratch/tree.tar" "$tree"
mkdir "$scratch/tree"
tar -xf "$scratch/tree.tar" -C "$scratch/tree"
cd "$scratch/tree"
"$tool" dir . --no-banner --redact --log-level warn --verbose --exit-code 1
