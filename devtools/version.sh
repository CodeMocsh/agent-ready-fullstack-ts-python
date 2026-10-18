#!/bin/sh
set -eu

[ "$#" -eq 1 ] || { echo "version: usage: version.sh <repo-root>" >&2; exit 1; }

FILE="$1/VERSION"

if [ ! -f "$FILE" ]; then
    echo "version: $FILE does not exist, and it is what decides the version." >&2
    exit 1
fi

CLAIMED="$(tr -d ' \t\n\r' <"$FILE")"

if ! printf '%s' "$CLAIMED" |
    grep -qE '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
    echo "version: VERSION reads '$CLAIMED'." >&2
    echo "version: that is not a semantic version, and a tag cut from it is permanent." >&2
    echo "version: write MAJOR.MINOR.PATCH, with nothing after it and no leading zero." >&2
    echo "version: 01.2.3 and 1.2.3 sort as one version and tag as two." >&2
    exit 1
fi

printf '%s\n' "$CLAIMED"
