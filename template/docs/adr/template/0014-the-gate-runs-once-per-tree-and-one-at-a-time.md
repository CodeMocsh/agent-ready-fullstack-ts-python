# 0014. The gate runs once per tree, and one at a time per project

Date: 2026-10-05

## Status

Accepted.

## Context

Repeated full checks occupy the same machine while agents wait to commit.
A retry can wait behind a run that already checked its tree.
A small local check gives earlier feedback. A full remote check retains broader coverage.
A hook runs only where a committer installed it. A commit through an API, a web editor or
`--no-verify` runs no hook. A coding agent that commits through an API is such a committer.
A coding agent often works in a sandbox with two cloud vCPUs. A slow commit gate costs it
many minutes before each commit. One downstream commit gate took 16 minutes there.

## Decision

The hook calls `make pre-commit`. Its runner calls `make gate-commit`.
This stage retains static checks, backend tests that do not start the app, and frontend Node
and smoke tests. Both halves exclude directories named `slow` at any depth.
`tests/test_commit_gate.py` holds the target boundaries.
`tests/slow/test_commit_gate.py` proves the backend selection.

A commit-gate test takes less than 0.5 seconds on the clock, on the reference core.
Its setup counts. A test that takes longer lives under its subject's `slow/` directory.
This rule keeps the commit gate fast enough for every committer, an agent in a sandbox included.
No test holds this rule. `docs/development.md` and `AGENTS.md` state it.
On another machine, divide 0.5 by the number that `backend/devtools/reference_core.py` prints.
That number converts CPU time. It also scales a wait, which takes the same time on every machine.

Test directories retain the subject they cover. The full run still selects a test under `slow/`.
The directory changes selection, not the test's environment or prerequisites. The Node and DOM projects retain their existing rules.
The external tiers remain separate: a slow unit test does not become an integration test.

`make gate` retains all default tests and static checks. `make suite` adds the frontend build.
`make suite` runs every check that `make gate-commit` runs, and every test that it selects.
`test_commit_gate.py` holds that.

Every committer runs the commit gate on the exact tree it commits.
The hook does that on a laptop. An agent that commits through an API runs `make pre-commit` first.

Something other than the committer checks a change before it merges.
That check need not run the commit gate again. It runs what the commit gate leaves out.
The generated GitHub workflow calls `make suite` on pull requests and pushes.
`test_workflow.py` refuses a workflow that copies the commands instead.
A project without that workflow supplies its own check before merge, and `make suite` before
promotion. A check after merge only stops promotion. It cannot keep a broken change out of `main`.
The opt-in tiers remain separate. A full suite does not claim they ran.

A matching pass returns before the lock.
Otherwise the runner takes `/tmp/<package_name>-pre-commit-<uid>.lock` and reads the tree and pass again.
Worktrees of one project queue. Different projects have different locks.
`devtools/hold-the-gate.pl` reports the checkout that holds the lock.
`tests/slow/test_gate_wait.py` proves that a waiter reuses a completed pass and fingerprints
after waiting.
It also proves that an already-passed tree does not wait behind another checkout.

The runner stores a matching tree in `gate-commit-passed` only after the checks succeed.
The tree includes tracked edits and untracked files. It excludes ignored files.
The runner records no pass if the tree changes during the run.
The record certifies only the commit gate. The full suite does not use it.

`BUDGET` limits the whole run, in seconds on the clock after the lock.
Many fast tests can still take too long together.
A committer waits that time, so the budget measures it directly.
`tests/devtools/slow/test_gate_budget.py` holds the budget.
`BUDGET` is sized for the smallest machine that commits: a cloud sandbox with two vCPUs.
Nothing else runs in that sandbox, so its clock measures the gate alone.
On 2026-11-17, three runs on an `E2_HIGHCPU_8` machine limited to two CPUs took 40 to 41 seconds.
`BUDGET` is about a third above the largest of the three runs.
The same gate took 12 to 34 seconds on an 18-core Mac at load 9 to 26.
Lock wait time consumes none of that budget.
`test_a_gate_that_waited_its_turn_is_not_charged_for_the_wait` holds that.
An over-budget run fails after recording its successful checks. A retry can reuse that record.
`test_a_green_run_over_budget_fails_and_is_still_remembered_as_passed` holds that.
Both tests are in `tests/slow/test_gate.py`.
A partial installation runs the available checks, names the omissions and records no pass.
CI calls the full target directly, so a missing half fails.

## Considered options

- Move every DOM test after merge. Cheap component and API smoke tests provide useful local feedback.
- Select tests from their last measured duration. Machine speed and fixture attribution change that selection.
- Replace the existing full target with a smaller target. Existing CI callers could silently lose coverage.
- Force all projects onto post-merge checks. That policy permits broken code on the shared branch.
- Replace an older waiter. A cancelled hook must fail, and each waiting commit needs its own result.
  Rechecking the pass after locking removes duplicate successful work without cancellation.
- Build a fair queue. The existing lock suffices for the smaller check until measurements show otherwise.

- Use `flock(1)`. macOS does not ship that command. Perl exposes the same lock operation.
- Use one lock per machine. Unrelated projects would block each other. The current lock coordinates one project.
- Fingerprint the index. Tests read the working directory, including edits not yet staged.
- Budget CPU-seconds on the machine that runs the gate. A cloud core uses three to five times the
  CPU of a Mac core for the same work. One number then fails on one machine, or allows too much on
  the other.
- Budget CPU-seconds on the reference core, converted with a timed workload. In a gVisor sandbox,
  every CPU clock moves in 10 ms steps, and under load each one reads 25 to 35 percent low. On an
  E2 machine, a busy hyperthread sibling doubles the CPU of the same work. On a Mac with cores of
  two speeds, a timing on one thread moved the converted number from 121 to 192. On an E2 machine
  limited to two CPUs, the conversion factor moved from 1.36 to 1.71 in three runs. The clock
  moved by one second. The committer waits on the clock, and the clock is correct in each case.
- Enforce the per-test limit in the test runners. A wider fixture charges its setup to the first
  test that uses it. A per-test limit then fails a test for cost the test does not own.
- Limit each test by its CPU alone. A test that waits out a time bound uses little CPU, and the
  committer still waits for it.

- Run only the hook. A fresh clone can have no hooks installed. CI checks that clone too.
- Run the commit gate again on every pull request. That repeats the work of the hook and slows
  every change. A check before merge runs the full suite, which covers more.
- Attest that the hook passed, and check the attestation at merge. Evidence that a check ran is
  weaker than a run of the check.
- Give a coding agent a gate of its own. Two commit gates drift apart. An agent runs the commit
  gate that every other committer runs.
- Skip the gate when nothing is staged. The working directory can still contain unchecked changes.
- Remove the budget. Small cost increases accumulate without a failing check.

## Consequences

A successful commit gate does not prove that the full suite passed.
The frontend configuration separates Node tests from DOM setup.
The full target includes every default test, live contracts and the production build.
A release caller binds its result to the exact source revision and stops on failure or absence.

The lock coordinates only calls through `make pre-commit`.
Direct test commands and other repositories can still compete for CPU.
On a laptop under heavy load from other work, the clock can pass `BUDGET`.
The retry then reuses the recorded pass.
A laptop shows growth later than the sandbox, because the budget is sized for the sandbox.

`perl` remains a prerequisite. `gate.sh.jinja` renders the project name into the lock path.
`test_the_gate_offers_no_way_to_switch_itself_off` refuses a bypass flag or variable.
