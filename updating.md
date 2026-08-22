# Updating and Maintenance

## For Projects Generated From This Template

Copier records your answers in `.copier-answers.yml`. Pull later improvements to this
template with:

```bash
uvx --exclude-newer "14 days" copier@9.17.1 update
```

Review the diff and resolve any `.rej` conflict files before committing. Two things about
this project make an update slightly different from a single-stack one:

- **`openapi.json` and `frontend/src/api/schema.ts` are contract artifacts.** If either
  lands in a `.rej`, do not resolve it by hand. Take whichever side you like, run
  `make openapi`, and commit what it produces. Hand-merging a generated file produces
  something
  neither the backend nor the generator would ever emit, and the next `make openapi-check`
  says so.
- **Run `make openapi` after any update that touched `backend/`.** `app.openapi()` is
  deterministic for pinned versions but not across FastAPI or pydantic minor bumps, so an
  update that bumps a floor can change the spec without anyone editing a model.

Finish with `make lint test`, which ends in the contract suite against a real backend.

## For This Template's Maintainers

This repo is the third sibling of
[agent-ready-ts](https://github.com/CodeMocsh/agent-ready-ts) and
[agent-ready-python](https://github.com/CodeMocsh/agent-ready-python). The three share no
code and no git history. What they share is the **agent-ready layer**, and keeping it in
step is a manual, deliberate process.

### The flow is one-way: this repo is a pure downstream consumer

**This repo never originates a shared-layer change.** A fix discovered here is PR'd to the
repo that owns the file, and pulled back once it lands.

| File | Owner |
|---|---|
| `template/.claude/hooks/agent_guard.py` | agent-ready-python |
| the Node guard, `agent-guard.mjs` (not shipped here) | agent-ready-ts |
| `template/frontend/devtools/complexity.mjs`, `conformance.mjs` | agent-ready-ts |
| `devtools/install-hooks.sh`, `template/devtools/install-hooks.sh` | agent-ready-python |
| the Entire hook wiring in `.claude/settings.json` and `.entire/` | agent-ready-python |
| the `AGENTS.md` *Approach* and *Zero comments* prose | agent-ready-python |
| the guard deny/allow tables and the hook-activation suite in `check_template.sh` | agent-ready-python |

Language-neutral files go to agent-ready-python purely as a tie-breaker. The rule matters
more than which repo won it: without one, a shared file has two upstreams and therefore
none.

**The agent-facing version of this is one line: in this repo, shared-layer files are
read-only; upstream the fix.**

The reason for the direction is churn. Re-pointing the flow through the newest and
least-proven of the three would make every shared-layer change wait on this repo's full
check run, which installs two toolchains and boots a server. The siblings' pairwise contract
already works, and this repo is the one with the most to lose from a bad shared-layer
commit, since it carries files from both.

A guard rule is the sharpest case. A rule added here and not upstreamed is a rule *missing*
from both siblings, and nothing anywhere fails when they disagree — the guard fails open, so
a divergence looks exactly like a clean run.

### What should stay in step

- `template/.claude/hooks/agent_guard.py` — byte-identical to agent-ready-python's. Same
  rule set, same fail-open behaviour, same messages.
- `template/.claude/settings.json` — the Entire hook wiring. Identical to
  agent-ready-python's, including the `PreToolUse` entry that runs the guard, because this
  repo ships the same guard.
- The repo's *own* `.claude/settings.json` and `.entire/` — all three repos run the Entire
  hooks on themselves, and deliberately **without** the agent guard: the guard is template
  output, not a rule the template lives under. Easy to miss, because it is the only part of
  the layer that sits outside `template/`.
- `template/AGENTS.md.jinja` — the *Approach* and *Zero comments* sections are meant to be
  word-for-word identical apart from language-specific examples. This repo's suppression
  list names all four suppressions — `biome-ignore`, `@ts-expect-error`, `# noqa` and
  `# type: ignore` — because it has both ecosystems. That is a language difference, not
  drift.
- `template/devtools/install-hooks.sh` and the repo's own `devtools/install-hooks.sh` — the
  shim installer, and the reasoning comment about `core.hooksPath` that comes with it.
- `template/frontend/devtools/complexity.mjs` and `conformance.mjs` — byte-identical to
  agent-ready-ts's, including the metric name recorded in baselines. A change to the metric
  that lands in only one repo silently invalidates the other's baselines.
- **`conformance.mjs` does not travel alone.** Two of its rules point at things outside the
  script, and a pull that takes the file by itself leaves a gate naming something that does
  not exist: `raw-stroke` and `magic-presentation-prop` tell you to set `--icon-stroke`, so
  `frontend/src/index.css` must declare it and carry it with
  `.lucide { stroke-width: var(--icon-stroke) }` in `@layer base`. The alpha rule's cost
  likewise depends on `frontend/components.json` pinning the `ui` alias to the same path
  `conformance.exclude` names. The fixture suite that asserts every rule in both directions
  is in `check_template.sh`, and a new rule upstream arrives with new rows in it.
- `template/docs/agent-tooling.md` — the *arguments* are shared: why a scanner is the wrong
  tool, why the guard fails open, why one threshold cannot catch structural erosion, why
  conformance fails closed. The measured numbers are not.
- The check-script contract: one script, called by CI and by the pre-commit hook, runnable
  on a laptop.
- **Vendored code as a category**, and now **contract artifacts** alongside it. Both
  siblings exempt tool-written code from the linter and from the zero-comments rule. This
  repo adds a second, stricter kind — a file that is regenerated rather than owned — and the
  distinction is worth carrying upstream as wording even where neither sibling has an
  example of it.

### What is per-repo, forever

**The stacks.** uv/ruff/BasedPyright/pytest/hatchling/PyPI in one sibling,
pnpm/biome/tsc/vitest/vite in the other, and both at once here. Do not port an idiom from
one ecosystem into the other because a sibling has it — port the *intent* and pick the tool
the ecosystem actually uses.

**The complexity thresholds, twice over.** This repo carries both sets, one per half, and
they were measured on their own sides with their own instruments: biome's *cognitive*
complexity against thirteen codebases on the frontend, ruff's *cyclomatic* complexity
against httpx and flask on the backend. Fifty *lines* and twenty-five *statements* do not
convert. A density per 1000 lines and a mean per callable are not the same kind of quantity,
which is why one ceiling is relative (`1.25x` the project's own origin) and the other
absolute (`3.0`). **Do not reconcile them, in either direction, in any repo.**
`template/docs/agent-tooling.md` states this in as many words, and it is the numbers there —
not the ones in either sibling's `updating.md`, which predate a metric redefinition — that
are current.

What *is* shared is the shape of the check, and the newest part of that shape is
two-sidedness: a baseline that only ever rises carries slack equal to however far the tree
has improved since, and that slack is spendable by the next commit. Both halves refuse a
rise and record a fall for you, from the fixing half of their lint only. The argument
carries whole across the two units even though none of the numbers do.

**The variant lists.** agent-ready-python varies on license *and* `publish_to_pypi`; this
repo generates an application, so it has no publish variant and its CI matrix is three
license variants. A new question in a sibling is not automatically a question here.

**The contract flow.** `openapi.json`, `schema.ts`, `make openapi`, the dual-run contract
suite, the `/api` proxy rewrite, the auto-422s: none of it has a counterpart in either
sibling, because neither generates a system with two halves that must agree. Nothing here
should be pushed upstream, and no sibling change should be expected to account for it.

**The generator mechanics.** This repo is Copier, like agent-ready-python; agent-ready-ts is
a bespoke Node CLI. Its `restoreDottedName` workaround, its `.if-license` suffix convention
and its `licenses/` directory exist to compensate for a renderer without an expression
language, and have no analogue here — see
[docs/adr/0001-copier-over-a-bespoke-cli.md](docs/adr/0001-copier-over-a-bespoke-cli.md).

**Conformance rule content.** `conformance.mjs` gates a Tailwind theme, a React hook and a
query cache. There is no Python analogue and inventing one would produce a check nobody
measured. What ports is the *selection rule*: gate what renders correctly on the screen the
agent is looking at and is wrong on one it never opens; leave anything an agent can verify
from its own diff to `AGENTS.md`.

### Known divergences

Things one repo has that another deliberately does not, recorded so the next person reading
a diff knows they are decisions rather than oversights.

- **One guard, not two.** This repo has a TypeScript half and still ships only
  `agent_guard.py`. The guard is worth the most at minute zero on a fresh clone, before
  either install has run, and `python3` is present then on macOS, Linux and every CI image
  while Node is not. Two implementations of one rule set is the drift this family exists to
  prevent. The reasoning is in `template/docs/agent-tooling.md`; if the Node guard gains a
  rule, that rule must reach `agent_guard.py` or this repo loses it.

- **File-length gating on one half only.** The frontend gates
  `style/noExcessiveLinesPerFile` at 500 non-blank lines across `src/**`, matching
  agent-ready-ts. The backend has none, matching agent-ready-python. Both positions are
  measured and both are correct on their own side.

- **The ratchets disagree about a missing baseline.** `complexity.py` prints a notice and
  passes when no baseline exists; `complexity.mjs` fails once the project is large enough to
  gate and no baseline has been recorded. A check that silently never turns on is not a
  check, so the frontend's posture is the better one — porting it into agent-ready-python's
  `complexity.py` is a change that belongs upstream, not here.

- **Tightening preserves `origin` on the frontend only.** Both halves lower a stale baseline
  from the fixing variant of their lint. The frontend's baseline carries two numbers, because
  its backstop is a multiple of where the project started, and tightening moves the drift
  reference while leaving `origin` alone — otherwise a codebase that improves and then
  regresses walks past the ceiling one recorded improvement at a time. The backend's ceiling
  is an absolute constant, so its baseline has nothing to preserve and this half of the rule
  has no counterpart there. Same divergence agent-ready-ts records against
  agent-ready-python, for the same reason.

- **No `.if-license` suffix and no `licenses/` directory.** Copier's Jinja does conditional
  filenames and an if/elif license body directly. Both of agent-ready-ts's mechanisms exist
  only because its renderer has no expression language.

### Checking for drift

There is no tooling for this. The honest process, assuming the siblings are checked out
beside this repo:

```bash
py=../agent-ready-python
ts=../agent-ready-ts

# The guard: byte-identical, no exceptions.
diff $py/template/.claude/hooks/agent_guard.py template/.claude/hooks/agent_guard.py

# The Entire wiring, in the template and in this repo itself.
diff $py/template/.claude/settings.json template/.claude/settings.json
diff $py/.claude/settings.json          .claude/settings.json
diff $py/.entire/settings.json          .entire/settings.json

# The hook installer, both copies.
diff $py/devtools/install-hooks.sh          devtools/install-hooks.sh
diff $py/template/devtools/install-hooks.sh template/devtools/install-hooks.sh

# The frontend gate scripts, including the metric name recorded in baselines.
diff $ts/template/devtools/complexity.mjs  template/frontend/devtools/complexity.mjs
diff $ts/template/devtools/conformance.mjs template/frontend/devtools/conformance.mjs
```

The shared `AGENTS.md` prose needs a range, because the section that follows it differs per
repo:

```bash
shared() {
  sed -n "/^## Approach/,/^## \(Build and Test\|Vendored\|The two halves\)/p" "$1" |
    sed '$d'
}

diff <(shared $py/template/AGENTS.md.jinja) <(shared template/AGENTS.md.jinja)
diff <(shared $ts/template/AGENTS.md)       <(shared template/AGENTS.md.jinja)
```

Anything that differs is either a deliberate language difference — a docstring against a
JSDoc, `# noqa` against `biome-ignore` — or drift. If you cannot tell which, it is drift.

### Owed upstream

**Nothing.** The comment gate landed here first, which is the direction this document
forbids, and it was carried rather than kept: `comments.py` and the `AGENTS.md` prose went to
agent-ready-python as [#16](https://github.com/CodeMocsh/agent-ready-python/pull/16) and
`comments.mjs` to agent-ready-ts as [#8](https://github.com/CodeMocsh/agent-ready-ts/pull/8).
Both merged, and both files are now byte-identical to their owners' copies — verified by
`diff`, which is the only thing that settles it.

Two things came back with them that are worth knowing, because both were found by the gate
rather than by review:

- agent-ready-python's `__init__.py.jinja` carried `# noqa: F403` and `# noqa: F405`. They are
  `per-file-ignores` in `pyproject.toml` now, under a heading, where an exception has to be
  argued for rather than typed. Two `# TODO` notes went with them — the rule already banned
  those and nothing had ever checked.
- agent-ready-ts needed no sweep: 24 files, none carrying a comment. The gate arrived over
  code that already satisfied it.

The scaffolding debt is settled too. It obligated two small PRs, one to each sibling, adding a
pointer paragraph to their own `updating.md` files: that a third repo now consumes the
agent-ready layer, that it never originates changes to it, and that a shared-layer change made
there should be expected to arrive as a PR rather than as a fork. Both landed —
agent-ready-ts#7 and agent-ready-python#15 — so the flow described above is now recorded at
both ends rather than only at this one. Neither sibling does anything differently; they just
know the layer has a third reader.

### A fourth reader, and it has drifted both ways

[fab](https://github.com/CodeMocsh/fab) is built from agent-ready-python and is a
downstream consumer of the same layer, which makes it worth diffing against for the same
reason the siblings are. Two divergences exist today, in opposite directions:

- **fab's `agent_guard.py` blocks `git commit` when no pre-commit hook is armed**, by
  reading `core.hooksPath` and `rev-parse --git-path hooks`. That is a genuinely good rule
  and this repo does not have it. It belongs in agent-ready-python, not here.
- **This repo's `rm` parsing is better than fab's.** `_rm_is_recursive_forced_and_its_targets`
  classifies every word rather than matching a leading flag group, so `rm / -rf` and
  `rm -rf -- /` are caught and fab's regex misses both. That fix is already upstream; fab
  has not pulled it.
- **fab has its own copy of `comments.py`**, written independently before this one existed.
  Now that agent-ready-python owns the file, fab's is a fork of something it consumes and
  should be replaced by a pull rather than kept in step by hand.

Neither is actionable here — the file is read-only in this repo — but a rule missing from
the guard fails open, so a divergence looks exactly like a clean run in both directions.
