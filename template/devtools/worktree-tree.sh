#!/bin/sh
set -eu

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

index="$(git rev-parse --git-path index)"
if [ -f "$index" ]; then
    cp "$index" "$scratch/index"
fi

GIT_INDEX_FILE="$scratch/index" git add -A
GIT_INDEX_FILE="$scratch/index" git write-tree
