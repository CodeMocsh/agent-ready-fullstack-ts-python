# Security

## How to report

Use GitHub's **private vulnerability reporting** on this repository — the *Security* tab, then
*Report a vulnerability*. **Do not open a public issue.**

Say what you did, what happened, and which version or commit you were on. Expect an
acknowledgement within a few days. If a fix ships, the advisory names you unless you ask it not
to.

## What is supported

The tip of `main`, and nothing behind it. This repo ships a template rather than a running
service, so a fix reaches a project already generated from it through
`copier update` — see [README.md](README.md) for that command. Older tags are history, not
supported releases.

## Some things are intentional, and none of them is a vulnerability

**A generated project authenticates nothing.** `tenant_for()` in `backend/app/identity.py`
resolves every request to the tenant `default`. Anyone who can reach the process can read and
write everything it holds. Every startup says so, and `UNAUTHENTICATED_IS_INTENTIONAL=1` is how
a deployment records that it meant it. Replacing that one function is the documented first step
before a generated project is reachable by anyone you do not trust.

**The agent guard fails open.** `.claude/hooks/agent_guard.py` refuses a small set of
unambiguously dangerous actions and allows everything it cannot parse. It is a seatbelt, not a
security scanner. Getting a command past it is expected, not a finding.

## What is in scope

- The generator writing a credential, a key or a live default into a project it renders.
- A rendered default that is unsafe where the documentation claims it is safe.
- The tenant isolation invariant not holding — row-level security is enabled *and forced* on
  every tenant table, and the decision record a generated project ships states what that
  promises.
- A supply-chain default that does not hold: the release cool-off on either half, pnpm's
  `trustPolicy`, or a dependency floor that resolves to something the pin was meant to exclude.
- A path through `devtools/check_template.sh` that reports a green gate over a check that did
  not run.

A weakness you introduce into your own generated project after rendering it is yours. The
template hands you a starting point and names what it does not do.
