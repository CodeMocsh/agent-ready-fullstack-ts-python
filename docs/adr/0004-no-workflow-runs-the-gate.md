# No workflow runs the gate in this repository

`.github/workflows/ci.yml` is gone from this repository. `make pre-commit` — here,
`devtools/check_template.sh` — is the gate, the git hook runs it, and nothing runs afterwards.

**Amended 2026-08-26, for `template/` only.** A generated project ships a workflow again, and
the reasoning below is why this repository still does not. The cost argument was never about
the generated project: what was expensive here is rendering the template three times per push,
once per license variant, and a generated project renders nothing — its workflow installs both
halves once and runs `make pre-commit`, which is the fast tier. The structural argument does
not carry either. It says CI here was a duplicate of the hook, and names what the duplicate
bought: *"a hook runs on the machine of whoever commits, against whatever they happen to have
installed, while a workflow ran against a fresh checkout nobody had configured."* That is a
different property rather than a copy, and it is worth more to a team of several people than to
one maintainer. **The decision below stands for this repository and is reversed for the one it
generates**, which is the asymmetry the original record did not separate.

**The immediate reason is cost.** This repository's workflow rendered the template three
times per push, once per license variant, and each render installed both toolchains, linted,
tested and built. That is the most expensive shape a check can have, and it bought the least:
the license variants differ by a `LICENSE` file and one line of `pyproject.toml`. The account
ran out of Actions credit, every job began failing before a runner was assigned, and a
workflow that is red on every commit is one nobody reads.

**The structural reason is that CI here was a duplicate, not an addition.** This project's
stated rule is that a check cannot exist in CI and be missing locally, so the workflow ran
the same script the hook runs. Removing it removes a copy, not a check — which is why this is
survivable at all, and it is the only reason it is.

## What it costs, precisely

**An unarmed clone commits unchecked, and nothing anywhere notices.** `make hooks` installs
the hook and `make install` calls it, but a clone that ran neither has no gate at all. This
is not hypothetical: it happened in this repository the day the rule was written, in a
worktree where the hook had never been installed — the commit ran nothing, and the only
reason it was caught is that `make check` had been run by hand minutes earlier.

**Nothing verifies what a reviewer sees.** A hook runs on the machine of whoever commits,
against whatever they happen to have installed. A workflow ran against a fresh checkout on a
machine nobody had configured, which is a different and stronger claim. `pnpm build` in
particular now runs only where someone chooses to run it.

The generated project keeps a smaller version of the same hole: its hook skips a half the
clone has not installed, says so on stderr, and exits 0.

## Considered options

**Keep the workflow, run one variant instead of three.** Cuts the bill by two thirds and
keeps server-side verification. Rejected only because there is no credit to spend at all; it
is the first thing to restore when there is.

**Keep the file, trigger it on `workflow_dispatch` only.** Costs nothing until run, and keeps
the pinned actions and the setup that took work to get right. Rejected because a workflow
nobody triggers is a workflow nobody maintains, and it would have kept every "CI runs this"
sentence in the docs true-in-principle and false-in-practice. Git history holds the file; a
deliberate restore is better than a dormant one.

**Delete the workflow and say nothing.** What the downstream project tried first, and
reverted: it removed the only server-side verification along with the failing jobs, silently.
The lesson taken from it here is that the hole gets written down rather than absorbed.

## Consequences

`make hooks` stops being a nicety. Both `AGENTS.md` files say so, and the generated
project's `README` says a commit that skipped the hook was checked by nothing.

`backend/tests/test_gate.py` no longer asserts that CI runs every gate member — there is no
CI to assert about. In its place, `test_a_workflow_runs_the_gate_rather_than_a_copy_of_it`
asserts nothing today and becomes load-bearing the moment a workflow appears: whatever
returns must run `make pre-commit` rather than re-listing its steps, because a re-listed copy
drifts in the direction of checking less.

To restore CI: add a workflow that runs `make pre-commit` (or `./devtools/check_template.sh
default` here), pin every action by commit rather than tag, and point `pnpm/action-setup` at
`frontend/package.json`, since this project has no root `package.json`. The deleted file did
all four and is in the history of this commit.
