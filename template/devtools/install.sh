#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

pnpm -C frontend install
(cd backend && uv sync --all-groups)
sh devtools/secrets.sh fetch
pnpm -C frontend openapi:types

if [ "$#" -gt 0 ]; then
    pnpm -C frontend exec playwright install "$@"
fi
