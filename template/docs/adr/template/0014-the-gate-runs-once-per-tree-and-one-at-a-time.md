# 0014. The gate runs once per tree, and one at a time per project

Date: 2026-10-05

## Status

Accepted.

## Context

The habit this template asks for is to run `make pre-commit`, then commit. The commit runs the
gate again through the git hook. With no memory of the first run, one change pays for the gate
twice. An agent whose tool call times out during the commit pays a third time on the retry. A
gate that takes minutes, run twice, is a gate people skip with `--no-verify`.

The gate runs every test in both halves, two servers and the contract suite, on every core it
can get. Two gates at once oversubscribe the machine. Each then fails the other's timeouts, and
that reads as a flaky suite.

A gate also gets slower one small step at a time. No single change looks like the cause, so
nobody notices until the gate takes minutes.

## Decision

`make gate` is the list of checks. `make pre-commit` runs that list through `devtools/gate.sh`,
and `.githooks/pre-commit` runs `make pre-commit`.
`test_the_hook_and_make_pre_commit_run_the_gate_through_one_runner` holds that chain. The runner
does what the list cannot:

- **It records the tree a green run read.** It writes the tree to `gate-passed` in the
  checkout's git directory. A later run over the same tree exits at once. The tree is the working
  directory's, which `devtools/worktree-tree.sh` reads through a copy of the index: tracked edits
  and untracked files, and nothing git ignores. The record is of the working directory, not the
  index, because the gate reads the working directory. The runner writes the record only when the
  tree after the run is the tree before it. A red run records nothing.
  `test_a_pass_is_remembered_for_exactly_the_tree_the_gate_read` and
  `test_a_red_gate_is_remembered_as_nothing` hold this.
- **It takes one lock per project before it runs anything.** The lock is
  `/tmp/<package_name>-pre-commit-<uid>.lock`. Two worktrees of one project queue. A second gate
  waits, and says on stderr which checkout holds the lock. `devtools/hold-the-gate.pl` takes it.
  `test_a_second_gate_waits_for_the_first_and_says_where_it_runs` holds this.
- **It measures the run in CPU-seconds against `BUDGET`.** The measure is the CPU time that
  `times` reports for the processes the runner waited for, and for the processes those waited for.
  The wait for the lock adds nothing to it, and the load from other worktrees changes it little. A
  green run over the budget fails after the runner records its pass, so a retried commit does not
  run the gate again. `make gate` uses 19 to 24 CPU-seconds on the eighteen-core machine where the
  template measured it, and the budget is about two and a half times that. A project whose gate
  grows raises `BUDGET` in a commit that says why.
  `test_a_gate_the_clock_alone_slows_is_within_budget`,
  `test_a_gate_that_works_past_its_budget_is_over_it` and
  `test_a_green_run_over_budget_fails_and_is_still_remembered_as_passed` hold this.
- **It runs what it can in a clone with one half installed, and says so.** It prints `PARTIAL RUN`
  and names each check it did not run. A partial run records nothing.

A CI runs `make gate`, not `make pre-commit`. In a project that ships
`.github/workflows/ci.yml`, that workflow does. In CI a missing half is a broken install, so a
partial run is wrong there. CI needs neither the record nor the lock.

## Considered options

- **Skip the gate when nothing is staged.** The gate reads files on disk, so the index is the
  wrong fingerprint.
- **`flock(1)`.** macOS does not ship it. `devtools/hold-the-gate.pl` makes the same call in perl,
  which macOS and Debian both ship.
- **One lock per machine.** Two projects compete for the same cores. But two different projects
  rarely gate at once. When they do, a person waiting for the other project's gate loses more than
  the contention costs.
- **No budget, and a person watches the time.** The time is printed on every run, and nobody reads
  it until the gate takes minutes.
- **A budget in seconds on the clock.** It measures the machine as well as the gate. Agents in
  several worktrees load one machine, and a gate whose checks all pass then fails on time
  alone.
- **Only the hook, no workflow.** The workflow checks a clone where `make hooks` never ran.

## Consequences

- `perl` is a prerequisite beside `python3`. `docs/installation.md` names it.
- A gate that waits for another one looks idle. It says on stderr whose turn it is.
- A commit fails when the gate is green but uses too much CPU. The person whose change uses more
  CPU finds the cause or raises `BUDGET` with a reason.
- The budget does not see a gate that gets slower without more work: a new wait, or a check that
  loses its parallelism. The runner prints the seconds on the clock beside the CPU-seconds, and
  nothing holds them.
- The measure grows a little with parallelism. Vitest runs at most one test file fewer than the
  number of cores at once. The template measured 5.7 CPU-seconds for its frontend tests one file
  at a time, and 7.5 with every file at once.
- `devtools/gate.sh` is rendered from `devtools/gate.sh.jinja`, because the lock carries the
  project's name.
- The runner keeps its jobs in one file: which halves are installed, the record, the lock, the
  budget and the partial run. Each one decides whether the next runs at all.
- A gate cannot be switched off. `test_the_gate_offers_no_way_to_switch_itself_off` refuses a
  variable or a flag that bypasses the hook or the runner.
