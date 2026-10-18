#!/bin/sh
set -eu

MARKER='# managed by: make hooks'

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Not a git repository yet; run 'make hooks' after git init."
    exit 0
fi

toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$toplevel" ]; then
    cd "$toplevel"
fi

for scope in --local --worktree; do
    current="$(git config "$scope" --get core.hooksPath 2>/dev/null || true)"
    if [ "$current" = ".githooks" ]; then
        git config "$scope" --unset-all core.hooksPath 2>/dev/null || true
    fi
done

hooks_dir="$(git rev-parse --git-common-dir)/hooks"
mkdir -p "$hooks_dir"
chmod +x .githooks/* 2>/dev/null || true

for hook in .githooks/*; do
    [ -f "$hook" ] || continue
    name="${hook#.githooks/}"
    dest="$hooks_dir/$name"

    chained=""
    slot=""
    n=1
    while [ -e "$dest.local$slot" ]; do
        if [ -f "$dest.local$slot" ]; then
            chained="$chained $name.local$slot"
        fi
        n=$((n + 1))
        slot=".$n"
    done

    if [ -e "$dest" ] && ! grep -qF "$MARKER" "$dest" 2>/dev/null; then
        if [ ! -f "$dest" ]; then
            echo "hooks: cannot activate $name: $dest exists and is not a regular file." >&2
            echo "hooks: move it aside, then re-run 'make hooks'." >&2
            exit 1
        fi
        mv "$dest" "$dest.local$slot"
        chmod +x "$dest.local$slot"
        chained="$chained $name.local$slot"
    fi

    {
        printf '%s\n' '#!/bin/sh' "$MARKER"
        if [ -n "$chained" ]; then
            cat <<EOF
hookdir="\$(dirname "\$0")"
for prev in$chained; do
    [ -x "\$hookdir/\$prev" ] || continue
    "\$hookdir/\$prev" "\$@" || exit \$?
done
EOF
        fi
        cat <<EOF
[ -x .githooks/$name ] || exit 0
exec .githooks/$name "\$@"
EOF
    } >"$dest"
    chmod +x "$dest"

    if [ -n "$chained" ]; then
        echo "hooks: $name -> .githooks/$name (chaining pre-existing:$chained)"
    else
        echo "hooks: $name -> .githooks/$name"
    fi
done

leftover="$(git config --get core.hooksPath 2>/dev/null || true)"
if [ -n "$leftover" ]; then
    configured="$(cd "$leftover" 2>/dev/null && pwd -P || true)"
    installed="$(cd "$hooks_dir" && pwd -P)"
    if [ "$configured" != "$installed" ]; then
        echo "hooks: core.hooksPath=$leftover does not resolve to $installed," >&2
        echo "hooks: so these hooks will not run. Unset it" >&2
        echo "hooks: (git config --unset core.hooksPath), or point it at that directory." >&2
        exit 1
    fi
fi
