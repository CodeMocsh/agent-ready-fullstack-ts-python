#!/bin/sh
# The one reader of VERSION. Prints the version it claims, or refuses and says why.
#
# One owner because the previous shape had two: a `case` glob in check_template.sh and a
# near-identical one in release.yml. They had already drifted apart -- the gate's copy grew a
# rule the workflow's copy did not have, and that rule was wrong -- which is the drift this
# repo refuses everywhere else by naming a target instead of re-listing it.
#
# An exact pattern, not a prefix glob. `[0-9]*.[0-9]*.[0-9]*` accepts `0.5.0oops` and
# `0.5.0.1`, and a tag is permanent: a version that is not a version becomes a release nobody
# can withdraw. Leading zeros are refused too, because `01.2.3` and `1.2.3` would sort as one
# version and tag as two.
#
# Usage: version.sh <repo-root>
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
    echo "version: write MAJOR.MINOR.PATCH, with no leading zeros and nothing after it." >&2
    exit 1
fi

printf '%s\n' "$CLAIMED"
