#!/bin/sh
# Activate the versioned hooks in .githooks/ for this clone.
#
# Installs one thin shim per committed hook into the repository's real hooks
# directory. The hook bodies stay in .githooks/ under version control; only the
# shim is local, which is the part git cannot track for you.
#
# Deliberately does NOT set core.hooksPath. That key is a poor fit here twice
# over: it lives in config shared by every worktree of the repository, so one
# clone enabling it silently changes hook resolution in its siblings, and it
# replaces the hook lookup wholesale rather than adding to it, so it disables
# hooks other tools have installed. Shims have neither problem -- the path is
# resolved per worktree, and a hook this repo does not ship is left untouched.

set -eu

MARKER='# managed by: make hooks'

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Not a git repository yet; run 'make hooks' after git init."
    exit 0
fi

# Paths below are relative to the worktree root, which is also where git runs
# hooks from. Anchor there so this works when invoked from a subdirectory.
toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$toplevel" ]; then
    cd "$toplevel"
fi

# Migrate clones set up by earlier versions, which pointed core.hooksPath at
# .githooks. Only our own value is removed; a deliberate setting is left alone.
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

    # A hook another tool installed here is moved aside and chained, never
    # skipped: skipping would leave this project's check uninstalled while
    # still reporting success, which is the failure this whole approach exists
    # to avoid. Both hooks then run, and a non-zero exit from either aborts.
    #
    # Slots already taken by earlier runs are collected first and keep their
    # place in the chain. Re-arming is routine -- 'make install' does it -- so
    # a tool that installs a hook after another one was already chained has to
    # extend the chain. Moving the newcomer onto an occupied .local would
    # delete the hook chained before it, silently.
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

    # Git runs hooks from the top of the working tree, so the relative path
    # resolves against whichever worktree is committing. A worktree on a branch
    # without this hook simply runs nothing.
    {
        printf '%s\n' '#!/bin/sh' "$MARKER"
        if [ -n "$chained" ]; then
            cat <<EOF
# Hooks from other tools were installed here first; keep running them, in the
# order they were chained.
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

# A hooksPath pointing somewhere else still wins over everything installed
# above, so the hooks would not run. Fail rather than report an activation that
# did not take. One that resolves to the directory we just wrote to is fine:
# aiming the key at the real hooks directory is a legitimate override, and it is
# what some tools tell you to do, so rejecting it would fail a working setup.
leftover="$(git config --get core.hooksPath 2>/dev/null || true)"
if [ -n "$leftover" ]; then
    # Compare resolved paths, not strings. The value may be relative or
    # absolute, and a path can reach the same directory through a symlink --
    # on macOS /var and /private/var are one place. A value that does not
    # resolve at all cannot be where the hooks are, so it fails below.
    # Relative values resolve from the worktree root, matching how git reads
    # them, because this script anchored there above.
    configured="$(cd "$leftover" 2>/dev/null && pwd -P || true)"
    installed="$(cd "$hooks_dir" && pwd -P)"
    if [ "$configured" != "$installed" ]; then
        echo "hooks: core.hooksPath=$leftover does not resolve to $installed," >&2
        echo "hooks: so these hooks will not run. Unset it" >&2
        echo "hooks: (git config --unset core.hooksPath), or point it at that directory." >&2
        exit 1
    fi
fi
