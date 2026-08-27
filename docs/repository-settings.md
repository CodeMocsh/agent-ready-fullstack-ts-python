# Repository settings

The configuration this repo depends on and cannot carry. Everything else here is a file that
fails when it is wrong; these are settings on GitHub that fail by quietly permitting something.

Written down because a setting nobody recorded is a setting nobody restores. A repo that is
forked, transferred or recreated arrives with every one of these at its default, and the
defaults permit what the rules below refuse.

## The ruleset on the default branch

Without this, the gate is decoration: a red tick and a green tick both allow the merge.

- **Require a pull request before merging.** Required approvals **0** — GitHub refuses a
  self-approval, so on a single-maintainer repo any higher number blocks the maintainer's own
  work and gets bypassed rather than satisfied. Zero approvals does not mean automatic: a
  person still presses merge.
- **Require the status check named `gate`.** That is the job name in
  `.github/workflows/check.yml`, and it is short and fixed for this reason. Renaming the job
  silently detaches the rule from the check.
- **Block force pushes, and block deletion.**
- **Require linear history**, and allow **squash** merges only. Every entry in `git log` is one
  landed decision, which is what makes the log readable as a record.
- **No bypass actors.** A bypass that exists is a bypass that gets used at the moment the gate
  is most inconvenient, which is the moment it is most load-bearing.

## Auto-merge stays off

Deliberate. The gate decides whether a change *may* land; a person decides whether it *does*.
Nothing in this repo enables it, and no workflow here holds `pull-requests: write`.

## Private vulnerability reporting is on

`SECURITY.md` sends reporters to it, so a repo where it is off sends them to a page that does
not exist and they open a public issue instead. It requires a public repository.

## The labels the issue templates apply

`.github/ISSUE_TEMPLATE/` names `bug` and `proposal`; `.github/dependabot.yml` names
`dependencies` and `template`. **A label a template names and the repository does not have is
applied silently as nothing** — GitHub does not warn, and the issue simply arrives unlabelled.
So all four have to exist here:

| label | for |
|---|---|
| `bug` | something the template renders, or the gate does, is wrong |
| `proposal` | a change the template should make, or stop making |
| `dependencies` | raised by Dependabot |
| `template` | touches `template/`, so it reaches every generated project |

## The order these are applied in

Two of them brick the repo if applied early:

1. `.github/workflows/check.yml` must be on the default branch **before** the ruleset requires
   `gate`. A required check that has never run leaves every pull request waiting forever,
   starting with the one that introduces the workflow.
2. Make the repository public **last**. Everything before it is reversible; the first public
   view is not.
