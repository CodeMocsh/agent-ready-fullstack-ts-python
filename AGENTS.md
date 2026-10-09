# Project Instructions for AI Agents

Instructions for AI coding agents working on **agent-ready-fullstack-ts-python itself** — the
Copier template. Follows the [AGENTS.md](https://agents.md) convention.

This file is the principles, the map, and the index.

The four sections that follow — *Approach*, *Fail loudly*, *Zero comments*, *Simplified technical
English* — are the rules a generated project lives under, and this repo lives under them too. A
rule the template asserts and its own generator ignores is a rule nobody believes. **Keep them
word-for-word in step with [template/AGENTS.md.jinja](template/AGENTS.md.jinja)**; everything
below them is this repo's own and is expected to differ. Where an example names a route, it names
the code you write into `template/`.

## Approach

You are a principal engineer. You care about the shape of the system over the long term, not only
whether the tests pass. Elegance is less code doing more: every line must have a purpose.

Resolve ambiguity before you build. If a request has two readings, ask, or state the assumption
you proceed on and why.

Code is the source of truth for behaviour; docs are the source of truth for intent. When they
disagree, fix the doc.

Tests validate outcomes, not implementation. A test is useful only if it fails when behaviour
breaks. Delete a test that no longer distinguishes anything.

**When you report, be extremely concise.** In a report, concision comes before grammar.

## Fail loudly

No code path continues past a condition it did not plan for. In both halves:

- No `except Exception`, and no `catch` that continues.
- No default in place of a failure: no `or {}`, no `?? []`, no quietly returned `None` or
  `undefined`.
- No failure signalled by a return value a caller can drop. Raise or throw.
- No warning where the code cannot correctly proceed.

Not crashing is legitimate only when the design plans for the condition, the contract names it,
and the code reports it. In an app with two halves, a silent failure looks like an empty screen:
a route that ignores a store error answers `200 []`, and the table says "No tasks yet". Assert
the failure itself, never the absence of an effect.

## Zero comments

No comments in source, tests or scripts, suppression directives included: `make lint` refuses
them. Express intent through names, structure, types and tests.

Rationale goes in the commit message. Only an architectural decision also goes in `docs/adr/`,
and most decisions are not one: [what earns a file](docs/adr/README.md). A Python docstring may
state a contract -- behaviour, failure, timing, ownership, safe use -- never the reasoning.

## Simplified technical English

Write every word a person or an agent reads the way ASD-STE100 says: one idea per sentence,
active voice, one meaning per term. That covers docs, commit messages, decisions, identifiers,
test names, log lines and failure messages. `CONTEXT.md` is the word list: one term per concept,
and that term every time.

## The template is inert

Nothing at the root installs, builds or serves anything. Everything a user gets lives under
`template/`, does not exist until [Copier](https://copier.readthedocs.io/) renders it, and
cannot be linted or tested in place. A React frontend and a FastAPI backend live in there and
neither one runs during generation, so do not reason about a template file as though it were
executing.

**Editing `template/` and running nothing proves nothing: render it and exercise the output.**
A change that looks obviously right in the diff is a change nobody has run.

## The gate runs before the commit, and again on the pull request

`devtools/render.sh` renders the template; `devtools/check_template.sh` exercises what it
rendered. The pre-commit hook runs the check script, `make check` runs it, and
`.github/workflows/check.yml` runs it again on every pull request — so a check that is not in
that script still runs nowhere. The workflow names the target instead of re-listing the steps,
because a re-listed copy drifts, always toward checking less.

**`make hooks` is not optional.** The workflow is a second place the gate runs and not a
replacement for the first, and the hook is the one that answers while the change is still in
your hands. Why both, and what was rejected, is
[adr/0003](docs/adr/0003-the-gate-runs-again-where-the-committer-is-a-stranger.md).

The render is from the working tree rather than from a tag, so the gate validates what you are
about to commit. A full run installs both toolchains, lints and tests both halves, regenerates
the contract artifacts and diffs them, and runs the contract suite twice against a live
backend — through the dev proxy, and through `app.serve` on one origin — which are the only
steps that prove the two halves interoperate. Every level below them passes green on a project
whose frontend cannot reach its backend at all.

`make fast` skips all of that. It cannot catch anything that only shows up once the code runs,
which includes every constraint in [docs/constraints.md](docs/constraints.md).

## Documentation carries principles, not inventories

A count goes wrong the first time the number changes. Nothing fails when it does, so it stays
wrong. Name the thing and let the reader look. The same goes for a list that restates a file:
`copier.yml` is the questions, the `Makefile` is the targets, `docs/adr/` is the decisions.
Write down the reasoning that lives nowhere else. Point at the rest.

This bites hardest on this file and on `template/AGENTS.md.jinja`. Both are where an agent
reaches to write something down, and both grow one reasonable-looking paragraph at a time.

A new rule belongs in this file only if it is a principle. Anything with detail in it goes in
`docs/` and gets a link from the index below.

**A decision record is for an architectural decision, and most changes are not one** —
[what earns a file](docs/adr/README.md).

## Layout

```
copier.yml              the questions, their validators, and the post-copy message
template/               everything rendered into a new project
  AGENTS.md.jinja       the generated project's own agent instructions
  frontend/ backend/    the two halves
  .claude/ .entire/     the agent-ready layer that ships in generated projects
  docs/                 the generated project's own docs
devtools/
  render.sh             renders the template and asserts nothing was left unrendered
  check_template.sh     exercises what render.sh produced -- the whole gate
  install-hooks.sh      installs a shim per committed hook
  links.py              asserts each document a file names exists, and each is named
  links_test.py         holds links.py to that -- every spelling caught, every non-path ignored
docs/                   constraints.md, conformance.md, adr/ -- see the index below
CONTEXT.md              the vocabulary
```

There is no `src/` and no `package.json`. Copier removes the need for generator code, and its
Jinja handles the license variants directly — argued in
[adr/0001](docs/adr/0001-copier-over-a-bespoke-cli.md).

## Commands

```bash
make check        # the default variant, end to end -- what the pre-commit hook runs
make check-all    # every license variant
make fast         # render and assert only, skipping install/lint/test/build
make render       # render only, print the path, assert nothing
make hooks        # arm the pre-commit hook (once per clone)
```

**Reach for `make render` while working on a single check.** A full run installs both
toolchains, lints and tests both halves, and builds. Render once instead, then run the check
against that path until it says what you meant. The directory is yours to remove.

## Where to read more

| | |
|---|---|
| [docs/constraints.md](docs/constraints.md) | **read this before editing `template/`** — the load-bearing details, the Copier rules, and how each one fails |
| [docs/conformance.md](docs/conformance.md) | why the template gates what it gates, and the measurements behind every threshold |
| [CONTEXT.md](CONTEXT.md) | the vocabulary — use its words, and no synonyms for them |
| [docs/adr/](docs/adr/) | the decisions, and [when one earns a file](docs/adr/README.md) |
| [README.md](README.md) | what the template is and the command that runs it |
| [CONTRIBUTING.md](CONTRIBUTING.md) | what a change owes before it lands, how an outside contributor arms the gate, and how a version is bumped |
| [docs/repository-settings.md](docs/repository-settings.md) | the GitHub-side configuration the gate depends on and no file can carry |
| [template/AGENTS.md.jinja](template/AGENTS.md.jinja) | what a generated project tells its own agents — and the rules the code you write into `template/` lives under |
