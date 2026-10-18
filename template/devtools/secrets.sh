#!/bin/sh
set -eu

case "${1:-}" in
    "" | fetch | tool) ;;
    *)
        echo "secrets: no such mode '$1'. Give no mode to fetch gitleaks when needed and scan, 'fetch' to fetch only, or 'tool' to print the path of the pinned gitleaks." >&2
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
