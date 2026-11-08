# 0014. The gate runs once per tree, and one at a time per project

Date: 2026-10-05

## Status

Accepted.

## Context

Repeated full checks occupy the same machine while agents wait to commit.
A retry can wait behind a run that already checked its tree.
A small local check gives earlier feedback. A full remote check retains broader coverage.

## Decision

The hook calls `make pre-commit`. Its runner calls `make gate-commit`.
This stage retains static checks, backend tests that do not start the app, and frontend Node
and smoke tests. Both halves exclude directories named `slow` at any depth.
`test_commit_gate.py` holds the target boundaries and proves the backend selection.

Test directories retain the subject they cover. An expensive test lives under that subject's
`slow/` directory. The full run still selects it. The directory changes selection, not the
test's environment or prerequisites. The Node and DOM projects retain their existing rules.
The external tiers remain separate: a slow unit test does not become an integration test.

`make gate` retains all default tests and static checks. `make suite` adds the frontend build.
The generated GitHub workflow calls `make suite` on pull requests and pushes.
`test_workflow.py` refuses a workflow that copies the commands instead.
A project without that workflow supplies its own full-suite caller before merge or promotion.
The opt-in tiers remain separate. A full suite does not claim they ran.

A matching pass returns before the lock.
Otherwise the runner takes `/tmp/<package_name>-pre-commit-<uid>.lock` and reads the tree and pass again.
Worktrees of one project queue. Different projects have different locks.
`devtools/hold-the-gate.pl` reports the checkout that holds the lock.
`test_gate_wait.py` proves that a waiter reuses a completed pass and fingerprints after waiting.
It also proves that an already-passed tree does not wait behind another checkout.

The runner stores a matching tree in `gate-commit-passed` only after the checks succeed.
The tree includes tracked edits and untracked files. It excludes ignored files.
The runner records no pass if the tree changes during the run.
The record certifies only the commit gate. The full suite does not use it.

The runner measures CPU against `BUDGET`. Lock wait time consumes none of that budget.
The 2026-11-07 reference run on macOS used 46.6 CPU-seconds and 61.8 elapsed seconds.
The 60 CPU-second budget leaves margin above that measured commit gate.
An over-budget run fails after recording its successful checks. A retry can reuse that record.
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
- Budget elapsed time. Other work changes elapsed time without changing the work this gate performs.

- Run only the hook. A fresh clone can have no hooks installed. CI checks that clone too.
- Skip the gate when nothing is staged. The working directory can still contain unchecked changes.
- Remove the budget. Small cost increases accumulate without a failing check.

## Consequences

A successful commit gate does not prove that the full suite passed.
The frontend configuration separates Node tests from DOM setup.
The full target includes every default test, live contracts and the production build.
A release caller binds its result to the exact source revision and stops on failure or absence.

The lock coordinates only calls through `make pre-commit`.
Direct test commands and other repositories can still compete for CPU.
The budget measures work, not elapsed delay. The runner prints both.

`perl` remains a prerequisite. `gate.sh.jinja` renders the project name into the lock path.
`test_the_gate_offers_no_way_to_switch_itself_off` refuses a bypass flag or variable.
