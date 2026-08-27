# Project Instructions for AI Agents

Instructions for AI coding agents working on **agent-ready-fullstack-ts-python itself** — the
Copier template. Follows the [AGENTS.md](https://agents.md) convention.

This file is the principles, the map, and the index.

The three sections that follow — *Approach*, *Fail loudly*, *Zero comments* — are the rules a
generated project lives under, and this repo lives under them too. A rule the template asserts
and its own generator ignores is a rule nobody believes. **Keep them word-for-word in step with
[template/AGENTS.md.jinja](template/AGENTS.md.jinja)**; everything below them is this repo's
own and is expected to differ. Where an example names a route or a mock handler, it names the
code you write into `template/`.

## Approach

You are a principal engineer. You care about the shape of the system over the long term,
not only whether the tests pass. Elegance is less code doing more: every line must earn
its keep.

Resolve ambiguity before building, not after. A request you could satisfy two different
ways is a request you do not understand yet. Ask, or state the assumption you are
proceeding on and why. Guessing and building is the expensive failure; asking costs one
round.

Code is the source of truth for behaviour; docs are the source of truth for intent. When
they disagree the doc is wrong. Fix the doc, not the code.

Tests validate outcomes, not implementation. A test earns its keep by failing when
behaviour breaks: one that mirrors the implementation only makes refactoring expensive,
and one that cannot fail is dead weight. Cover the seams and the edges that actually
bite. Coverage percentage is not the goal, and deleting a test that no longer
distinguishes anything is a real improvement.

## Fail loudly

No code path continues past a condition it did not plan for. Four bans, and both halves
are in scope:

- No `except Exception`, and no `catch` that continues.
- No default standing in for a failure — no `or {}`, no `?? []`, no quietly returned
  `None` or `undefined`.
- No failure signalled by a return value a caller can drop. If continuing would be
  wrong, raise or throw.
- No warning where the code cannot correctly proceed.

Not crashing is legitimate only when all three of these hold: the design plans for the
condition, the contract names it, and the code reports it. Fewer than three, and it
raises.

This is not a style preference, because in an app with two halves **a silent failure
looks like an empty screen**. A route that swallows a store error answers `200 []` and
the table renders "No tasks yet". A `catch` around a mutation leaves the optimistic
update on screen and the write on the floor. A mock handler that answers a case the
backend refuses makes the contract suite green against a shape the real service never
returns. Every one of those reads as working software — and a test that asserts the
*absence* of an effect passes just as happily against the bug. Assert the failure
itself.

The contract is where this is cheapest to get right: declare every status code a route
can return, with a model for anything that has a body, and the other half then cannot
fail to handle it without a type error.

## Zero comments

No explanatory comments, no docblocks, no TODO/FIXME notes, no lint or type suppression
directives (`biome-ignore`, `@ts-expect-error`, `@ts-ignore`, `# noqa`, `# type: ignore`),
no commented-out code. Shebangs and TypeScript `///` directives are executable
directives, not comments.

Express intent through names, structure, types, and tests. Rationale goes in the commit
message; a decision goes in `docs/adr/`. This relocates rationale rather than removing it,
so a repo that adopts the rule and still writes `fix: bug` has simply deleted the
explanation.

**An ADR cites nothing by section number.** Name the thing — the route, the function,
the rule, the invariant — and let the reader grep. A section number points into one
revision of one document, and an ADR outlives the document whose structure it borrowed.

One carve-out: a published public API. Where a package is consumed outside this repo its
exported surface carries JSDoc or a docstring, because that text is shipped documentation,
not an explanation aimed at a reader of the source. Everything internal stays bare. Such a
doc states contracts, not reasoning: behaviour, failure, timing, ownership, safe use. Link
the rationale; don't restate it.

Scope: source, tests, scripts. Config files may carry comments where the format offers no
other way to explain a rule, and that includes `frontend/*.config.ts`, which is
configuration that happens to be written in TypeScript. **Vendored and generated code is
out of scope entirely.**

**The rule is enforced, because on its own it does not hold.** `make lint` fails on any
comment token under `frontend/{src,tests,e2e,devtools}` and `backend/{app,tests,devtools}`
— `frontend/devtools/comments.mjs` and `backend/devtools/comments.py` are the two gates.
An agent reads this file at the top of a session and explains in place anyway, because
that is what the training data does. The suppression half is the half most worth
mechanising: refusing the spelling turns a threshold decision taken silently at the point
of pain into either a fix in the code or a reviewable line in `biome.json`,
`tsconfig.json` or `pyproject.toml`.

## Simplified technical English

Every word a human reads is written the way ASD-STE100 says to write a maintenance manual: one
idea per sentence, active voice, and one meaning per term. That covers prose and code alike —
docs, commit messages, decisions, identifiers, test names, log lines, and the message a failure
carries.

**Take the rules, not the dictionary.** ASD-STE100 ships a controlled vocabulary chosen for
aircraft maintenance, and this repo has its own nouns. `CONTEXT.md` is the word list that binds
here: one term per concept, and that term every time the concept appears — in a sentence, in a
symbol name, in the text of an error.

This is what makes *Zero comments* affordable. With no comment to fall back on, the name and the
failure message are the whole explanation, so they are worth the care the code gets.

## The template is inert

Nothing at the root installs, builds or serves anything. Everything a user gets lives under
`template/`, does not exist until [Copier](https://copier.readthedocs.io/) renders it, and
cannot be linted or tested in place. A React frontend and a FastAPI backend live in there and
neither one runs during generation, so do not reason about a template file as though it were
executing.

**Editing `template/` and running nothing proves nothing: render it and exercise the output.**
A change that looks obviously right in the diff is a change nobody has run.

## The gate lives before the commit, and nowhere else

`devtools/render.sh` renders the template; `devtools/check_template.sh` exercises what it
rendered. The pre-commit hook runs the check script, `make check` runs it, and no workflow
ships — so a check that is not in that script runs nowhere, and an unarmed clone commits
unchecked with nothing anywhere saying so. **`make hooks` is not optional.** The cost of that
arrangement is recorded in [docs/adr/0004](docs/adr/0004-no-workflow-runs-the-gate.md).

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

**A decision record is for a decision, not for an explanation.** The test is the cost of change:
a choice earns a file when undoing it later would be expensive, and one you could make
differently next week is an implementation detail. Most changes need neither —
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
| [template/AGENTS.md.jinja](template/AGENTS.md.jinja) | what a generated project tells its own agents — and the rules the code you write into `template/` lives under |
