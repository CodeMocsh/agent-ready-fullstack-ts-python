# 0003. The gate runs again where the committer is a stranger

Date: 2026-10-05

## Status

Accepted.

## Context

`devtools/check_template.sh` is the whole gate. The pre-commit hook runs it, but only on a
machine whose owner ran `make hooks`.

A pull request from a fork comes from somebody who did not arm the hook and may not want to. A
full run installs two toolchains, starts servers and takes tens of minutes. No mechanism makes a
hook reach that contributor, and the pull request does not show whether the hook ran. A gate that
runs only in the hook checks nothing on the changes that nobody here wrote.

A reviewer cannot close that gap by reading the diff. A change that looks right in a diff is a
change nobody has run. This repo says that about its own template, and it is true of a reviewer
too.

A hook and a workflow check different things. A hook checks the machine of the person who
commits. A workflow checks a fresh checkout that nobody configured. The template ships a workflow
to every generated project for that reason. A template that asserts this rule and does not follow
it asserts a rule that nobody believes.

The gate also costs real time. A render for each license variant on every push is minutes, not
seconds.

## Decision

- `.github/workflows/check.yml` runs `make check` on every pull request. It runs
  `make check-all` on every push to main, on a weekly schedule and on a manual dispatch.
  `make check-all` renders every license variant.
- The workflow names the make target and does not list the steps of the gate. A listed copy
  drifts toward checking less. The drift shows as a green pull request that the hook would have
  refused. `devtools/check_template.sh` refuses a `check.yml` that does not run `make check`, or
  that calls `check_template.sh` directly.
- The weekly run exists because the dependency floors, the release cool-off and the audit
  resolve against a registry that changes while this repo does not. A tree that passed last month
  can fail today with no commit between. Without the schedule, the first person to see that is a
  contributor whose pull request goes red for a reason that is not theirs.
- The workflow reads its toolchain pins for pnpm, Node and uv from `template/` at run time. A
  version written in two places goes stale in one of them, and nothing fails when it does.
- `make hooks` stays mandatory. The workflow is a second place the gate runs, not a replacement
  for the hook. A contributor who learns on GitHub what the hook could have said before the
  commit pays for the same result twice. The hook gives the result locally.

## Considered options

**Arm the hook, and attach a signed attestation that it passed.**
[firstmate](https://github.com/kunchenguid/firstmate) does this with
[no-mistakes](https://github.com/kunchenguid/no-mistakes): an attestation bound to the head
commit, and a required check that refuses a pull request without one. That is correct when the
gate is an agent-driven pipeline that a runner cannot reproduce, because then the attestation is
the only evidence. This gate is a deterministic shell script with no model in it. A runner can
run it. Evidence that a check ran is weaker than a run of the check. Rejected.

**Run only `make fast` on a pull request.** It takes seconds and asserts the render. It cannot
catch a failure that shows only when the code runs. That includes every constraint in
[../constraints.md](../constraints.md). A green result that means less than it seems to is worse
than no result. Rejected.

**Keep the hook as the only gate, and merge on trust.** An unchecked contribution then lands
without a trace. The first person to find the defect is a user who generates a project from it.
Rejected.

**Run every license variant on every pull request.** It multiplies the cost of each pull request.
One variant on a pull request, and every variant on main, gives the same coverage before a
release. Rejected.

## Consequences

- A change costs a full run on GitHub runners as well as one on the contributor's machine.
- The runner has a Docker daemon, so the gate there runs the Postgres tier. On a machine with no
  Docker, `devtools/check_template.sh` prints that it skipped the tier. So the gate on a pull
  request is stronger than the gate on most machines.
- A license variant other than the default can break on a pull request and fail only after the
  merge to main.
- A maintainer can merge on a green result. The merge decision moves from reading a diff to
  reading a result. A maintainer with no time to review each pull request by hand can still keep
  the repo correct.
