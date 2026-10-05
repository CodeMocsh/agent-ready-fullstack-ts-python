#!/bin/sh
# The tree this checkout's working directory would commit: tracked edits and untracked files
# alike, and nothing git ignores. Read through a copy of the index, so fingerprinting the tree
# stages nothing.
set -eu

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

index="$(git rev-parse --git-path index)"
if [ -f "$index" ]; then
    cp "$index" "$scratch/index"
fi

GIT_INDEX_FILE="$scratch/index" git add -A
GIT_INDEX_FILE="$scratch/index" git write-tree
