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

released() {
    curl -fsSL -o "$4" "https://github.com/$1/releases/download/$2/$3" && return 0
    echo "secrets: the release link of $3 failed. Fetching it through the GitHub API." >&2
    asset="$(curl -fsSL "https://api.github.com/repos/$1/releases/tags/$2" | awk -v name="\"$3\"," '
        $1 == "\"url\":" && $2 ~ /\/releases\/assets\// { url = $2 }
        $1 == "\"name\":" && $2 == name { gsub(/[",]/, "", url); print url; exit }')"
    [ -n "$asset" ] || {
        echo "secrets: the GitHub API names no asset $3 in the release $2 of $1" >&2
        return 1
    }
    curl -fsSL -H "Accept: application/octet-stream" -o "$4" "$asset"
}

tool="$root/build/tools/gitleaks-$version/gitleaks"
if [ ! -x "$tool" ]; then
    archive="gitleaks_${version}_${platform}.tar.gz"
    sum="$(grep "  $archive\$" devtools/gitleaks.sums)" || {
        echo "secrets: devtools/gitleaks.sums has no checksum for $archive" >&2
        exit 1
    }
    released gitleaks/gitleaks "v$version" "$archive" "$scratch/$archive"
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
"$tool" dir . --config .gitleaks.toml --no-banner --redact --log-level warn --verbose --exit-code 1
