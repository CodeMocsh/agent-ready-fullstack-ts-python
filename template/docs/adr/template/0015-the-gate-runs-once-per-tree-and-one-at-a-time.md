# The gate runs once per tree, and one at a time per machine

`make gate` is the list of checks. `make pre-commit` runs it through `devtools/gate.sh`, and the
git hook runs `make pre-commit`. The runner does two things the list cannot.

- **It records the tree a green run read**, in `gate-passed` under the checkout's git
  directory. A later run over the same tree exits at once. The tree is the working directory's,
  read through a copy of the index by `devtools/worktree-tree.sh`: tracked edits and untracked
  files alike, and nothing git ignores.
- **It takes one lock per machine** before it runs anything. A second gate waits, and says which
  checkout holds the lock.

## Why

**Once per tree.** The habit this template asks for is to run `make pre-commit` and then commit.
Without the record, that habit runs the whole gate twice for one change. An agent whose tool
call times out during the commit's gate pays a third time on the retry. The sibling project
this template was extracted alongside watched its gate grow to many minutes, and a second run
of that is what people learn to skip with `--no-verify`.

The record is for the working directory, not the index, because the gate reads the working
directory. A tree the gate did not read never matches: an unstaged edit or a new file changes
the fingerprint, and `test_a_pass_is_remembered_for_exactly_the_tree_the_gate_read` holds that.

**One at a time.** The gate installs nothing but runs every test in both halves, two servers
and a browserless contract suite, on every core it can get. Two gates at once — two
worktrees, or two projects — oversubscribe the machine twice, and each fails the other's
timeouts, which reads as a flaky suite. The lock is in `/tmp` and named for the user, not the
project, because a second project contends for the same cores.

## Considered options

- **Skip the gate when nothing is staged.** The gate reads files on disk, so the index is the
  wrong fingerprint.
- **`flock(1)`.** macOS does not ship it. `devtools/hold-the-gate.pl` is the same call in perl,
  which macOS and Debian both ship.
- **A lock per project.** Two projects still share the cores.
- **A time budget that fails a slow run, as the sibling project has.** Its number is about twice
  what one project's gate costs on one team's machines. A template cannot pick it for every
  project, and a wrong number fails commits for no reason. A project that wants one adds it.
- **Drop the workflow, as the sibling project did.** It dropped GitHub Actions only because a
  private dependency would not install there. That does not apply to a generated project, and
  the workflow is what checks a clone where `make hooks` never ran.

## Consequences

- `perl` is a prerequisite, beside `python3`, and `docs/installation.md` names it.
- A gate waiting for another one looks idle. It says on stderr whose turn it is.
- CI runs `make gate`, not `make pre-commit`. The runner passes a clone with one half missing,
  which is right on a laptop and wrong in CI, where a missing half is a broken install. CI needs
  neither the record nor the lock.
- The runner keeps its four jobs in one file — which halves are installed, the record, the
  lock, and the partial run — because each one decides whether the next runs at all.
- The helper keeps the name `worktree-tree.sh`, which the sibling project uses, so the next
  `copier update` there adds nothing beside it.
