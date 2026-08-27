# Contributing

Thank you for looking. This is a Copier template, so a change here reaches every project
generated from it afterwards, and `copier update` carries it into projects that already exist.
That is the reason the bar below is where it is.

## Arm the hook before your first commit

```bash
make hooks
```

`devtools/check_template.sh` is the whole of the enforcement. The pre-commit hook runs it, and
`.github/workflows/check.yml` runs the same script on every pull request, so a change nobody
armed a hook for is still checked.

**Arm it anyway.** Only the hook answers while the change is still in your hands.

## The template is inert, so a diff proves nothing

Nothing at the root installs, builds or serves anything. A React frontend and a FastAPI backend
live under `template/` and neither one runs until [Copier](https://copier.readthedocs.io/)
renders it. **Render your change and exercise the output.**

```bash
make render      # render only, print the path -- reach for this while iterating
make fast        # render and assert, skipping install, lint, test and build
make check       # the default variant, end to end -- what the hook runs
make check-all   # every license variant
```

If your change alters the screen, run `make screenshots`. It renders a project, drives the real
app in both modes and rewrites `docs/img/`, so the README shows what the template generates
today. It is not in the gate: it fetches a browser, which nothing else here does, and a picture
cannot fail a check.

`make check` installs both toolchains, lints and tests both halves, regenerates the contract
artifacts and diffs them, and runs the contract suite twice against a live backend. It takes
minutes. While working on one check, run `make render` once and run that check against the path
until it says what you meant.

## What a change owes

- **A check, if the change is a rule.** A check that is not in `devtools/check_template.sh` runs
  nowhere. A rule nothing enforces does not hold — the repo says so about its own comment ban,
  and means it.
- **A commit message carrying the rationale.** Comments are banned in source, so the reasoning
  has to land somewhere. The title is a claim in the present tense; read `git log` for the
  shape.
- **A decision record, if a reasonable person would undo it.** The test is the cost of change,
  and [docs/adr/README.md](docs/adr/README.md) draws the line. Most changes need no record.
- **The vocabulary in [CONTEXT.md](CONTEXT.md).** One term per concept, and that term every
  time — in prose, in a symbol name, and in the text of a failure.

Read [AGENTS.md](AGENTS.md) before you write anything. It carries the rules the code lives
under: *Approach*, *Fail loudly*, *Zero comments*, and *Simplified technical English*. They
apply to human contributors exactly as they apply to agents.
[docs/constraints.md](docs/constraints.md) carries what breaks far from the edit that broke it.

## Pull requests

One decision per pull request. Say what you changed, and why the obvious simpler version does
not work. That second half is the whole of what the template asks for, because it is the only
part neither the gate nor a reviewer can reconstruct from the diff.

There is no checklist to tick. `make check` runs on the pull request itself, `make check-all`
runs on main, and both render the template and exercise the output — so a box claiming you ran
it would be a weaker answer to a question already answered. Run it locally anyway, because
finding out here is slower for you than finding out there.

A merge needs the gate green, and a person still presses the button — nothing here merges
itself. What enforces that lives on GitHub rather than in a file, so it is written down in
[docs/repository-settings.md](docs/repository-settings.md).

## Bumping the version

What users get is a **git tag**, because Copier resolves to the newest one rather than to
`main` — so an untagged merge reaches nobody, however long it has been on the default branch.
`VERSION` is not a manifest; nothing at this root is a package. It is one line saying which tag
should exist.

The tag is not written by hand. **`VERSION` decides it** — one line, the number this repo
claims to be at. The gate reads it on every pull request, so a value nothing can tag fails
while it is still a pull request. When the gate passes on main and no release answers that
claim, `.github/workflows/release.yml` creates the tag and the release. It runs on a
*completed* gate rather than on the push, so a tag is never cut from a tree that has not
passed.

GitHub writes what changed, from the pull requests merged since the previous tag. Nothing here
keeps a hand-written list: one would say what somebody remembered to copy, and this says what
landed.

So a release is a one-line edit inside a pull request, reviewed alongside the change it
describes:

1. Edit `VERSION`.
2. Merge. The tag and the release appear on their own.

**The number describes what `copier update` does to a project that already exists**, not how
much the code changed. That is the only question a user of a template can act on:

- **Patch** — a fix. The update applies cleanly and changes nothing a project answered.
- **Minor** — a new capability. The update is a clean three-way merge, and
  `copier update --defaults` stays a no-op for a project that wants none of it. Every new
  question needs a behaviour-preserving default for this to hold; `copier.yml` says so where
  the questions are.
- **Major** — the update will conflict or needs a manual step afterwards: a file that moved, an
  answer whose meaning changed, or a question with no default that preserves today's behaviour.

A large diff that lands cleanly is a minor. A one-line change that renames a file every project
has edited is a major. Size is not the test; cost to the person updating is.

## Reporting something

A vulnerability goes to [SECURITY.md](SECURITY.md), never to a public issue. Anything else is
an issue. Conduct is [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
