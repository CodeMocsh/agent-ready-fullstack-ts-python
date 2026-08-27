# The gate runs again where the committer is a stranger

## Status

Accepted. Supersedes the position taken when the workflow was deleted, which
[CHANGELOG.md](../../CHANGELOG.md) records under v0.4.0.

## Context

`devtools/check_template.sh` is the whole of the enforcement, and until now the pre-commit
hook was the only thing that ran it. That held while every commit came from a machine whose
owner had run `make hooks`.

Opening the repository ends that. A pull request from a fork is written by somebody who never
armed the hook and may not want to: a full run installs two toolchains, starts servers, and
takes tens of minutes. There is no mechanism by which a hook reaches them, and no way to tell
from the pull request whether it ran. So the gate that is described as the whole of the
enforcement enforces nothing on exactly the contributions nobody here wrote.

The maintainer cannot close that gap by reading the diff. A change that looks obviously right
in the diff is a change nobody has run — this repository says so about its own template, and
the same sentence applies to a reviewer.

The argument that removed the workflow was about cost, and it was a real one: a render per
license variant on every push, on a repository whose gate is minutes rather than seconds.
That argument survives, and it is answered by running one variant on a pull request and every
variant on main rather than by running nothing.

The structural argument does not survive, and it was already abandoned once. A generated
project got its workflow back on the reasoning that a hook checks the machine of whoever
commits while a workflow checks a fresh checkout nobody configured, and that this is a
different property rather than a second copy of the first. A template that ships that
reasoning to every project it generates and declines it for itself is asserting a rule its own
generator ignores.

### Considered options

**Require contributors to arm the hook and attest that it passed.** This is what
[firstmate](https://github.com/kunchenguid/firstmate) does with
[no-mistakes](https://github.com/kunchenguid/no-mistakes): a signed attestation, bound to the
head commit, that a pipeline ran — and a required check that refuses a pull request without
one. It is the right answer when the gate is an agent-driven pipeline that a runner cannot
reproduce, because then the attestation is the only evidence there is. This gate is a
deterministic shell script with no model in it. A runner can simply run it, and evidence that
something ran is strictly weaker than having run it. Rejected as a layer that buys nothing
here.

**Run only `make fast` on a pull request.** Seconds instead of minutes, and it asserts the
render. It also cannot catch anything that only shows up once the code runs, which is every
constraint in [../constraints.md](../constraints.md) and every reason this repository says a
diff proves nothing. A green tick that means less than it appears to is worse than no tick.

**Keep the hook as the only gate and merge on trust.** This is the status quo, and it fails
in the direction that leaves no trace: an unchecked contribution lands, and the first person
to find out generates a project from it.

## Decision

`.github/workflows/check.yml` runs `make check` on every pull request and `make check-all` on
every push to main. It names the target rather than re-listing the steps, so the workflow and
the hook cannot drift — a re-listed copy drifts toward checking less, and that drift surfaces
as a green pull request that a commit would have refused.

It also runs weekly on a schedule. The dependency floors, the release cool-off and the audit
resolve against a registry that moves while this repository does not, so a tree that passed
last month can fail today with no commit in between. Without the schedule the first person to
discover that is a contributor whose own pull request goes red for a reason that is not theirs.

The pins the workflow needs are read out of `template/` at run time rather than written into
it. A version spelled in two places goes stale in one of them, and nothing fails when it does.

**`make hooks` stays not optional.** The workflow is a second place the gate runs, not a
replacement for the first. A contributor who finds out on GitHub what they could have found
out before committing has paid for the discovery twice, and the hook is what makes the
feedback local.

## Consequences

The claim that the gate lives before the commit and nowhere else is no longer true. Every
place asserting it -- `AGENTS.md`, `README.md`, `CONTRIBUTING.md` and the pre-commit hook's
own header -- is corrected rather than left to rot.

Landing a change now costs a full run on GitHub's runners as well as one locally. The
runner has a Docker daemon, so it also runs the Postgres tier that a laptop without one
prints as skipped — the gate on a pull request is strictly stronger than the gate on most
laptops.

A green tick is now a thing a maintainer can merge on. That is the point: it moves the
decision from reading a diff to reading a result, and it is what makes the repository
maintainable by somebody who does not have time to review every pull request by hand.
