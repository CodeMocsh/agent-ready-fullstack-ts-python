# Agent tooling

This project ships three guardrails for AI coding agents: a guard on dangerous actions, a
complexity gate on each half, and a set of conformance checks on the frontend. All are
optional — delete the files and their config to drop any of them.

## The agent guard

Coding agents run shell commands and write files on your behalf. Most of what they do is
fine; a small number of actions are catastrophic and irreversible.

A thorough security scanner is the wrong tool here. It needs rules, updates, and a
dependency, and it earns its keep by finding subtle problems — while the actions that
actually destroy an afternoon are blunt and few.

`.claude/hooks/agent_guard.py` blocks a small set of unambiguous, high-severity actions via
Claude Code's `PreToolUse` hook:

- `rm -rf` targeting `/`, `~`, or the project root
- raw-disk writes (`mkfs`, `dd of=/dev/…`)
- fork bombs
- force-push to `main`/`master` (`--force-with-lease` is allowed)
- `git commit --no-verify`
- writing `.env` files (`.env.example` and friends are allowed)
- writing recognisable secrets: known token prefixes and private keys

**One guard covers both halves, and it is the Python one.** A repository with a TypeScript
half could plausibly ship a Node guard instead, but two implementations of one rule set is
exactly the drift this project's guardrails exist to prevent: a rule added to one becomes a
rule missing from the other, and nothing fails when they disagree. Which single interpreter
wins is settled by when the guard is worth the most, which is minute zero on a fresh clone —
before `pnpm install` or `uv sync` has run, when an agent is at its least informed about the
repository and most likely to reach for something blunt. `python3` is present at that moment
on macOS with the command line tools, on every mainstream Linux, and in every CI image; Node
ships with nothing and arrives only once someone installs it. The guard itself uses the
standard library only, so it also runs before `uv sync` has created a virtualenv.

It **fails open**. Any parse error, unexpected payload shape, or bug in a rule allows the
action rather than blocking legitimate work — and the hook wrapper in
`.claude/settings.json` exits cleanly when `python3` is absent rather than failing the tool
call. A guard that blocks real work gets deleted; one that occasionally misses stays
installed.

Coverage is deliberately narrow. A determined or unusual dangerous action passes. This pairs
with review, it does not replace it.

Because it fails open, a silent failure looks identical to a clean run. Changes to the rules
should be exercised against the guard's test cases rather than assumed.

To tune it, edit the patterns. To remove it, delete the file and its `PreToolUse` entry in
`.claude/settings.json`.

## Complexity gating

Code written iteratively by agents degrades in a measurable way: new logic gets patched into
functions that are already complicated, rather than distributed into focused ones.
[SlopCodeBench](https://arxiv.org/pdf/2603.24755) measured this across 15 agents and found
structural erosion rising in 77% of trajectories.

A single threshold cannot catch it. A per-function limit misses a codebase where every
function creeps to just under the line. A whole-codebase aggregate misses one catastrophic
function among many small ones. A ratchet against yesterday's value misses slow drift
accepted one approved increase at a time.

So each half runs several thresholds, each catching what the others structurally cannot, and
each half runs them in its own instrument and its own unit. Nothing is ever averaged across
the two — see *The two halves are not comparable* below.

### The frontend half

Measured against thirteen well-regarded codebases using biome's own counter, so the number
in your editor, in `pnpm lint`, and in CI is always the same number. Four are React
applications — bulletproof-react, excalidraw, documenso's app and its UI package — and nine
are TypeScript libraries: ky, ofetch, tinybench, ts-pattern, hono, zod, vitest, remeda,
valibot.

Biome measures **cognitive** complexity, which weights nesting and does not count each
boolean operand separately. It is not the cyclomatic complexity the backend half is gated
on: a trivial function scores 0 rather than 1, JSX contributes almost nothing, and the tail
is much fatter.

**Per-function: `maxAllowedComplexity = 15`** (biome
`complexity/noExcessiveCognitiveComplexity`). Runs in the editor as you type. Across the
thirteen it flags 0.00–5.59% of functions; React sits at the comfortable end (bulletproof
0.00%, documenso 0.97–1.05%, excalidraw 3.91%) precisely because JSX adds structure without
adding branching. It is also biome's default, inherited from SonarSource's calibration; the
measurement is what says the default is right here, rather than the other way round.

**Per-function volume: `maxLines = 50`** (biome `complexity/noExcessiveLinesPerFunction`,
blank lines skipped). Complexity and length are independent, and more so here than under
cyclomatic counting: a sixty-line run of markup or assignments has a cognitive complexity of
**0**. React components do run longer than library functions — 5.3–5.8% of functions exceed
50 lines in excalidraw and documenso's app, against 2.4–3.5% across the libraries — but
bulletproof-react, the reference architecture closest to this project's frontend, sits at
0.9%. A well-structured component does not fight this.

**Per-file volume: `maxLines = 500`** (biome `style/noExcessiveLinesPerFile`, blank lines
skipped, `src/**` only). Measured the same way: non-blank lines per authored source file,
tests and type declarations excluded. bulletproof-react has **no file over 200 lines at
all** across 104 files, at a median of 35 and a 95th percentile of 162, so 500 is two and a
half times the largest file in the closest thing to a model answer. The two React products
in the set are structurally unlike this app — a monorepo of routes, and a canvas editor —
and sit at 2.3% of files over the line (documenso's app), 4.4% (its UI package) and 7.7%
(excalidraw's app). The libraries run from 0.4% (valibot) through 1.5% (remeda) and 3.2%
(hono) to 8.3% (vitest) and 15.2% (zod).

This one is a backstop, not a ratchet, and that is the trade. At 500 the check fires on a
file that has genuinely run away — one that became a module of its own without anyone
deciding it should. It says nothing about a component drifting to 480 lines, which is the
shape accretion usually takes; what holds that line is the 50-line function gate, since a
React component is mostly one function. It gates `src/**` and nothing else: a test file's
length is a function of how many cases it holds, and `devtools/` scripts are single-file on
purpose, dependency-free so they run before `pnpm install` ever has.

Told a file is too long, split it at a seam — a component, a hook, a module — never at line
500. Length is the symptom; cohesion is the property, and no linter measures that one.

**Drift: `tolerance = 0.02`** on *complexity density*, against a committed baseline
(`frontend/.complexity-baseline.json`), checked by `frontend/devtools/complexity.mjs`.
Density is each function's cognitive complexity **beyond its first branch**, capped at 15,
summed over the codebase, per 1000 non-blank source lines. So a function scoring 6
contributes 5, one scoring 40 contributes 14, and one scoring 0 or 1 contributes nothing.

That floor is not an approximation, it is the definition, and the definition was chosen to
match what can be measured exactly. Biome's `maxAllowedComplexity` bottoms out at 1, so it
will not report a function scoring 1 at all. Rather than sum "every function" and quietly
miss those, the metric charges nothing for a function's first branch; every function is then
accounted for at its exact contribution, and the number the script prints is the number the
definition describes. Capping at 15 is what makes this complementary to the per-function
gate rather than redundant: it measures only the mass that gate cannot see.

Measured, not guessed: across 200 commits of hono's history the largest single-commit rise
was +0.82%, and across 200 commits of zod's it was +0.60%. Neither had a single commit
exceed 2%, so the tolerance leaves roughly 2.4x headroom over the worst real one. The
baseline records which metric produced it — `complexity-beyond-first-branch-per-kloc` — and
comparing against a baseline recorded under a different definition fails loudly rather than
being silently absorbed.

**The rule is two-sided, because a baseline that only ever rises is not a gate.** Whatever
the tree has improved since the baseline was recorded is slack, and slack is added to what
the next commit may spend: a baseline sitting 2% above the tree admits a single change
raising density by 4.08%, roughly five times the largest move hono ever made in one commit.
So a density more than `tolerance` *below* the baseline means the baseline is stale.
`make lint` lowers it; `make lint-check` — and so the pre-commit hook — refuses and
says to. Same shape as the formatter: the fixing variant fixes, the checking variant
refuses.

The two directions are deliberately asymmetric about who decides. A rise means someone is
consenting to more complexity, so it stays a manual `pnpm complexity:baseline` that lands in
a diff for review. A fall needs no approval — nobody has to sign off on code getting better
— but it must not be forgettable, so it is recorded for you. Tightening at one `tolerance`
rather than on every improvement is what keeps the baseline out of the churn: density moves
by hundredths constantly, and a file rewritten on each of those is a merge-conflict
generator. The price is that worst-case slack is two tolerances, 4.08%, instead of one.
Bounded and stated beats tight and churning.

Tightening lowers `density` and leaves `origin` alone, so improving the code never lowers
the ceiling with it.

**Ceiling: `ceilingFactor = 1.25`** — density may never exceed 1.25× the `origin` recorded
in the baseline the first time one is written. The ratchet only ever compares against
yesterday, so many individually-approved increases drift a long way with every step
consented to.

The ceiling here is deliberately *relative*, where the backend's is absolute, and the
measurement is the reason. No aggregate cognitive-complexity metric sits in a tight band
across good codebases: density runs from 21.8 (remeda) to 110.9 (vitest) across the
libraries and 24.2 (bulletproof-react) to 88.5 (excalidraw) across the React apps. Within a
single project it is remarkably stable — hono moved only 101.6→104.5 across 200 commits — so
the honest form of the check anchors to where *this* project started rather than to a number
borrowed from someone else's.

The codebase-wide checks stand down while the project is too small to gate. The rule is
derived rather than picked: one new function at the cap moves density by `14000 / lines`, so
while that swing is larger than the tolerance allows, a single perfectly legal function
could fail the build on its own. The check computes this against the project's own density
every run, because React applications are less dense than libraries and a hard-coded line
count would switch the check on too early in exactly the better-structured projects.

Density falling to zero — no function branching beyond a single condition — is the one
improvement the two-sided rule would otherwise miss, and the slack left behind there is not
a fraction of the baseline but all of it, so `make lint` records that too. Putting the
complexity back is then a rise from zero rather than a free ride: there is no level to
measure a relative rise against, so the check refuses and asks for
`pnpm complexity:baseline`, which lands in the diff for review.

### The backend half

Measured across httpx and flask with ruff's *cyclomatic* counter, again so that the editor
and `make lint` report one number.

**Per-function: `max-complexity = 8`** (ruff `C901`, in `[tool.ruff.lint.mccabe]`). p95
cyclomatic complexity across httpx and flask is 6 and p99 is 9–11, so 8 flags roughly the
top 2% of what excellent Python does and leaves normal code alone. Below 8 it starts
flagging functions those maintainers shipped happily.

**Per-function volume: `max-statements = 25`** (ruff `PLR0915`). Complexity and volume are
independent — a sixty-line run of assignments scores 1 on `C901`, which is blind to it.
Across httpx and flask the median function is 4 statements, p95 about 20 and p99 about 35,
so 25 sits near p97: the same place `max-complexity` sits on its own distribution. Tighter
starts flagging cohesive 21-statement functions, and splitting those produces single-caller
helpers, which is its own kind of mess.

**Drift: `tolerance = 0.05`** on mean complexity against a committed baseline
(`backend/.complexity-baseline.json`), checked by `backend/devtools/complexity.py`. Catches
everything fattening below the per-function gate. Measured, not guessed: across 60 commits
of flask's history the largest single-commit move in mean complexity was 0.0437.

Two-sided here for the same reason it is on the frontend, and the argument above carries
whole: a baseline that only ever rises carries slack equal to however far the tree has
improved since, and that slack is spendable by the next commit. A mean more than `tolerance`
*below* the baseline means the baseline is stale, so `make lint` lowers it and
`make lint-check` refuses. A rise stays a manual `--update-baseline`. Worst-case slack is
two tolerances, 0.10, rather than unbounded. This half has no `origin` to preserve when it
tightens — its ceiling is an absolute constant, not a multiple of where the project started.

**Ceiling: `ceiling = 3.0`** on mean complexity, absolute rather than relative, because mean
cyclomatic complexity does sit in a tight band across good Python: httpx 2.2, flask 2.3.

Both codebase-wide checks stay off below `min-callables = 50`. A mean over a handful of
functions is meaningless — adding one complexity-8 function to six moves it by 0.86 — so a
new project would otherwise fail on the first real function anyone writes.

**There is deliberately no file-length limit on this half**, where the frontend has one. The
empirical work on module size and defects is contested, both httpx and flask ship files over
a thousand lines, and a service module that grows by adding routes is not the shape a React
`src/**` tree grows in. The frontend's 500-line backstop was set by measuring React
codebases and holds there; nothing measured says it transfers here.

ruff follows the `mccabe` convention, which does not count boolean operators. A function of
the form `if a and b and c and d and e` scores 6 where radon scores 26. No threshold setting
closes that gap; it is the price of one shared counter.

### The two halves are not comparable

The numbers on the two sides are in different units, on different scales, produced by
different instruments, and calibrated against different bodies of code. Cognitive complexity
has a floor of 0 and a fat tail; cyclomatic complexity has a floor of 1 and a tight one.
Fifty *lines* and twenty-five *statements* do not convert. A density per 1000 lines and a
mean per callable are not the same kind of quantity at all, which is why one ceiling is
relative and the other absolute.

So there is no combined number, no average across the repository, and no reason to expect
the two baselines to move together. Anyone tempted to unify them is proposing to replace two
measured thresholds with one unmeasured one. Raising a threshold to make a build pass
defeats all of this on either side: split the function instead. If a rise is genuinely
warranted, `pnpm complexity:baseline` on the frontend and `--update-baseline` on the backend
record it deliberately and land it in the diff for review. The zero-comments rule in
`AGENTS.md` bans `biome-ignore`, `# noqa` and `# type: ignore`, so there is no quiet way
around the per-function gates either — which is the point.

## Conformance checks

`frontend/devtools/conformance.mjs` runs in `pnpm lint`, in `pnpm lint:check`, and through
`make lint`. It gates eleven patterns biome has no rule for. They have one thing in common:
each renders correctly on the screen the agent is looking at, and is wrong somewhere the
agent never looks.

That is the whole selection rule. A convention an agent can verify by reading its own diff
belongs in `AGENTS.md`, where it costs nothing. A convention that only fails on a screen
nobody opened needs a check.

**The theme is the only place a colour is defined** — `raw-colour`, `palette-utility`,
`named-colour`. Dark mode in this project is entirely the `@theme` block and `.dark` in
`frontend/src/index.css`. A `bg-white`, a `#f5f5f5`, a `bg-blue-500` or a
`bg-[rebeccapurple]` renders perfectly in light mode and is still exactly that colour in
dark mode, because nothing in the theme reaches it. An agent does not switch the theme
before calling a change done, so the check stands in for looking. The literal forms — hex,
`rgb(`, `oklch(`, and the CSS colour keywords — are caught wherever they appear, which
covers a colour set in an inline style without needing a separate rule about inline styles.
The keywords are matched only where a colour can actually go, so a variable named `tan` or
the string `"orange"` in data is not a violation. Across bulletproof-react, documenso,
shadcn-ui and excalidraw the rule found three instances in total, all three in documenso's
signature-pad colour picker — which is exactly what `conformance.allow` is for. Widening the
utility shape below did not move that: re-run across the same four, `named-colour` returns
the same count it did before, and the whole widening costs twelve additional
`palette-utility` hits in total — ten in documenso, two in shadcn-ui, every one of them a
real `border-t-*` or `divide-y-*` palette step. A hole that stayed open since the rule
shipped turns out to be cheap to close, which is the argument for closing it rather than
noting it.

**A colour utility is not always `prefix-value`, and the rules that assumed it were
porous.** Two ordinary Tailwind shapes defeated the first versions of `palette-utility` and
`named-colour`, and neither is exotic. A *side segment* can sit between the prefix and the
value — `border-t-red-500`, `border-x-blue-200`, `divide-y-gray-300`, `ring-offset-blue-200`
— and a rule anchored straight to `border-` walks past every one of them. A *compound
prefix* puts the familiar word second — `inset-shadow-red-500`, `inset-ring-primary/50`,
`drop-shadow-xl/25` — where the word-boundary anchor that stops `converted-to-black` from
firing also stops `shadow` from matching inside `inset-shadow`. The same anchor that makes
the rule safe on prose made it blind here.

So the three colour rules now share one builder rather than three hand-kept lists: the
utility set, one optional side segment, then whatever value the rule is looking for. The
side segment is spelled as Tailwind's actual vocabulary — `t r b l x y s e`, `offset`,
`shadow` — rather than "any word", because "any word" would fire on `text-on-black` in a
sentence. Sharing the builder is what keeps the three from drifting apart again, which is
how the gap opened: `palette-utility` carried its own copy of the utility list, and the copy
is what went stale.

**An opacity modifier is a colour the theme never declared** — `token-alpha`, on
`bg-primary/80`, `text-subtle/60`, `ring-destructive/40`. The token survives the theme
switch; the `/80` does not, because nothing declared it. It is the same argument as
`raw-colour` made one level in: the value is decided at a keystroke rather than once, and
the second call site that wants the same fade picks `/75`.

The remedy is deliberately *not* a fade per colour. `--color-primary-80` alongside
`--color-destructive-50` and `--color-muted-30` is a combinatorial theme, and alpha is the
axis that fits a ramp worst, because the fade is almost always contextual — a scrim, a
hover wash, a focus ring. Name the role instead: `--color-scrim` is one decision that
holds, and it reads at the call site.

Tailwind spells two unrelated things with a slash, and only one of them is alpha, so the
exemption is anchored to the one prefix that carries the other: `text-sm/6` and
`text-2xl/8` are the font-size and line-height shorthand and go free. Nothing else does.
`shadow-lg/50` really is shadow opacity and really is caught, which is the point — an
exemption written by shape rather than by prefix would have taken `bg-base/50` and
`ring-lg/40` with it, and `base` is a plausible surface-token name.

This is the most expensive rule in the file, and the number is worth knowing before you adopt
it. It is a range rather than a figure, and which end you land on is decided by your
directory layout rather than by your code.

With each tree's vendored UI excluded, `token-alpha` fires in 1.2% of source files in
bulletproof-react, 4.4% in shadcn-ui, 6.1% in documenso and 0% in excalidraw. Over every file
instead, the same rule reads 4.8% to 8.9%. The spacing rule finds 0% to 0.6% either way, so
this is somewhere between two and fifteen times that, and the gap between the two readings is
almost entirely vendored code: 175 of documenso's hits are its `packages/ui` primitives, and
2,522 of shadcn-ui's are the v4 registry and docs site.

**The low end assumes a clean split, and the exclusion is path-based, so it is only ever as
clean as your paths.** This template earns the assumption by construction —
`frontend/components.json` pins the `ui` alias to `@/components/ui` and
`conformance.exclude` names that same path — but plenty of projects drop generated components
straight into `src/components` beside code they wrote, and no exclusion separates the two
afterwards. Such a tree gets the upper figure. That is a fact about the layout, not about the
rule, and it is the first thing to check before reading a violation count as a verdict on the
code.

The split is partial even here, which is worth knowing before the first `shadcn add`. Only
`registry:ui` items go to the `ui` alias. A `registry:component` or `registry:block` — the
blocks and the charts — lands in `frontend/src/components`, a `registry:hook` in
`frontend/src/hooks`, a `registry:lib` in `frontend/src/lib` beside the one excluded
`utils.ts`. All of those are scanned, and
all of them arrive written in exactly the idiom these rules gate. Fixing the values is the
better answer, because a block you added is a block you will edit; adding the file to
`conformance.exclude` is the honest alternative. Reaching for `conformance.allow` is the wrong
one — it is keyed on matched text, so a `/80` allowed for a vendored block is a `/80` allowed
everywhere.

The rate is not the deciding number anyway. In bulletproof-react, the tidiest of the four, the
rule condemns **no file that was not already failing** — every hit lands in a file some other
rule had already caught, so the gate's verdict on that codebase is unchanged. A tree adopting
the rule mid-life should still expect a grandfathering commit rather than a clean run.

The bracketed spelling is the same offence and is caught too: `bg-primary/[0.31]` and
`ring-ring/[.08]` are exactly `/80` with more punctuation. What is *not* the offence is a
fade that reads a custom property — `bg-card/(--wash)`, `bg-primary/[var(--my-alpha)]` —
because that value was declared somewhere and is reviewable there. That is the same
carve-out `arbitrary-spacing` grants, spelled the same way in the pattern, and it is the
line worth holding: the rule is against the literal, not against the brackets.

`inset-shadow-sm/30` and `drop-shadow-xl/25` are caught too, through the compound prefixes
described above — this rule shares the same utility set, so a family added for one colour
rule is a family the other two gain at the same time.

**One type scale** — `arbitrary-type`, `raw-type-declaration`, `inline-type-declaration`.
The objection to `text-[13px]` is not that 13px is wrong; it is that a scale with one value
outside it has stopped being a scale, and the next agent copies the exception rather than
the rule. Sizes, families, leading and tracking are defined in `@theme` and reached through
utilities.

**One spacing scale, and only for rhythm** — `arbitrary-spacing`, on `p-`, `m-`, `gap-` and
`space-`. Same argument, and the measurement says it holds: across bulletproof-react,
documenso, shadcn-ui and excalidraw, arbitrary padding, margin and gap appear in 0% to 0.6%
of component files — none at all in bulletproof-react, one in documenso's app. The scale is
granular enough that `p-[7px]` almost never means "no token fits"; it means someone
eyeballed it.

Sizing is deliberately exempt: `w-[240px]`, `max-w-[65ch]`, `min-h-[200px]`. There is no
token for "the sidebar is 240px", and the same measurement finds sizing arbitraries
outnumbering spacing ones by roughly thirty to one — 830 against 28 in shadcn-ui, 249
against 1 in documenso's app. A rule covering both would fire constantly on correct
Tailwind, and a rule that fires on correct code gets deleted. An arbitrary value that reads
a theme variable — `gap-[--spacing(var(--gap))]`, `pt-[calc(var(--gap)*0.25)]`, both taken
from shadcn-ui — is also exempt, because reaching for the theme is the point rather than the
offence.

**Stroke weight has one home** — `raw-stroke`, `magic-presentation-prop`. `--icon-stroke`
is declared in `frontend/src/index.css` and `.lucide { stroke-width: var(--icon-stroke) }`
in `@layer base` carries it to every icon, because lucide-react tags all of them:
`Icon.mjs` builds `className: mergeClasses("lucide", contextClass, className)`. A CSS
declaration outranks an SVG presentation attribute, so the token wins over the
`stroke-width="2"` lucide renders by default, and one line themes every icon in the app.

Both rules exist because a stroke weight has two doors that set the same property:
`strokeWidth={1.5}` in JSX, and `stroke-2` or `stroke-[3px]` as a utility class. They fail
in opposite directions, so neither stands in for the other. The utility *beats* the token —
`@layer utilities` sorts after `@layer base` — so an ungated `stroke-2` silently wins. The
prop *loses* to it, so an ungated `strokeWidth={3}` does not render wrong, it renders at
1.5 with no error at all. One is a value that escapes the theme; the other is a line of
code that already does nothing. Both are worth a failure.

The digit is the whole test. `strokeWidth={MARK_TRUNK_WIDTH}` passes and
`strokeWidth={1.5}` fails, because a named constant is already decided once — the same
carve-out sizing gets from the spacing scale, for the same reason. Hand-drawn geometry
stays possible; deciding its weight twice does not.

Which `stroke-*` spellings are widths at all is the fiddly part, and the rule follows what
Tailwind actually compiles rather than what the prefix looks like. `stroke-2`, `stroke-[2px]`,
`stroke-[0.5]`, `stroke-[.5]` and `stroke-[-1]` are all stroke-*width* and all caught — the
leading `.` and `-` matter, because a check that only understood a leading digit would let
`stroke-[.5]` through while catching `stroke-[0.5]`, which is the same number. Both
`stroke-(length:--icon-stroke)` and `stroke-[length:var(--icon-stroke)]` are widths that read
the theme, and both go free. `stroke-muted-foreground`, `stroke-[#fff]`, `stroke-[red]` and
`stroke-[currentColor]` are *paints*, not widths, and belong to the colour rules instead.

`stroke-[var(--icon-stroke)]` is the trap in that list, and it is the reason the doc names
the working forms explicitly. Tailwind cannot infer a type from a bare variable, so it
resolves to the `stroke` paint and sets the icon's colour to `1.5`. The `length:` hint is
what makes it a width.

Measured across the same four, the two stroke rules cost far less than the alpha one.
`raw-stroke` finds three instances in total — one in documenso, two in shadcn-ui — because a
stroke width set through a class is rare. `magic-presentation-prop` fires in 0.2% to 1.2% of
source files as configured, inside the band the spacing rule already occupies, and like
`token-alpha` condemns no file in bulletproof-react or excalidraw that was not already
failing.

Excalidraw is the shape of adoption worth expecting, and it is not the shape the percentage
suggests: 165 of its 168 hits sit in a single file, `packages/excalidraw/components/icons.tsx`,
a hand-rolled icon sprite. That is the honest failure mode for this rule — not a rate spread
across a codebase but one wall in one file. A project that hand-rolls its icons excludes that
file and moves on; a project using lucide and `--icon-stroke`, which is what this template
ships, never writes the prop at all.

Three known limits, stated rather than hidden. An identifier carrying a digit — `SIZE_2` —
false-positives, and `conformance.allow` is the answer. Like every other entry in the table
these match line by line, so a prop split across lines is missed; making it exact would cost
a bespoke scan of the whole JSX attribute rather than a table row, which is not worth it for
a prop that fits on one line in every real case. And the prop rule reads the `=` form only,
so a weight set through a `style` object is not caught — inline `style` is already the
narrowest of the doors, and widening the pattern to the `:` form cost more than it bought:
it fired on `strokeWidth: 1 | 2` in a type and on any object literal that happened to carry
the key.

**Data does not come from an effect** — `effect-data`, a `useEffect` whose body contains
`await`, `.then(` or `fetch(`. TanStack Query is already in the project. An effect that
fetches skips its cache, its deduplication, and the loading and error states the rest of the
UI is written against, and it works in the demo that made it. Effects that subscribe, add a
listener, or touch the DOM imperatively are legitimate and untouched.

What is deliberately *not* enforced: which size a heading takes, how many sizes a screen
uses, whether the hierarchy reads well. Those are judgement, they differ per project, and a
gate on them would fire on legitimate work. Taste belongs in `AGENTS.md` and in looking at
the screen.

### It fails closed, where the guard fails open

The two guardrails point in opposite directions on purpose, and the asymmetry follows the
cost of a false positive on each side. A guard false positive blocks a command an agent
needs and burns an afternoon, with no cheap way to get past it in the moment; so the guard
allows anything it is not certain about. A conformance false positive blocks a commit and
costs one line of config; so the checks refuse anything they are not certain about.

`conformance.allow` in `frontend/package.json` takes the exact matched text, so a genuine
brand colour has a home — in the diff, where it gets reviewed. That reviewability is what
makes failing closed affordable: the escape hatch exists, and using it is a visible act
rather than a silent one. `conformance.exclude` mirrors the paths that `biome.json` and
`complexity.exclude` already carry, for code this project did not author.

### Why a script and not biome rules

Biome has no rule for Tailwind tokens; `noHexColors` reaches CSS only, and the nursery rules
that come closest are renamed and promoted between minor releases, which would break
`pnpm lint` on an upgrade. A script also has somewhere to put an exception. The one check
biome
*does* have — the per-file limit — is configured in `biome.json` rather than reimplemented
here, so it lights up in the editor as you type.

The colour and scale rules match line by line. The two structural checks work on a copy of
the file with strings and comments blanked out, positions preserved, so a `")"` in either
cannot end an effect body early and the word `fetch(` in a note cannot condemn an effect
that only subscribes. The inline-style rule reads only what is inside a `style` attribute's
own braces, so a prop or a type named `fontSize` is not a violation. Those are
approximations, chosen to keep the script dependency-free: a regex literal ending in `//`
still reads as a comment, and a style object built in a variable and passed in by name is
not followed. Both are narrow and both fail towards silence, which is the right direction
for a check that cannot be suppressed.

### There is no backend analogue

The conformance checks gate a Tailwind theme, a React hook and a query cache. None of those
has a Python counterpart, and inventing one to make the halves symmetrical would produce a
check nobody measured. What ports across the two halves is the *selection rule*, not the
rules: gate what renders correctly on the screen the agent is looking at and is wrong on one
it never opens, and leave anything an agent can verify from its own diff to `AGENTS.md`.
Whatever the backend's version of that turns out to be, it is not these eleven patterns.

## The comment gate

`AGENTS.md` bans comments outright — rationale goes in the commit message, a decision goes
in `docs/adr/`, and only a published package's exported surface carries a docstring or
JSDoc. The rule is the interesting part. The gate exists because the rule does not hold on
its own: an agent reads `AGENTS.md` at the top of a session and explains in place anyway,
because that is what the training data does.

Two scripts, one per half, both wired into `make lint`:

- `frontend/devtools/comments.mjs` over `src`, `tests`, `e2e` and `devtools`
- `backend/devtools/comments.py` over `app`, `tests` and `devtools`

**Neither greps.** The Python one tokenizes, so a `#` inside a string or a URL is not a
finding. The JavaScript one walks the file as a sequence of regions — quoted strings,
template literals, line comments, block comments — and reports only the comment ones, which
is the same scanner `conformance.mjs` uses to ignore strings. That matters more here than it
looks: `devtools/complexity.mjs` documents `--exclude` in a multi-line template literal
containing `/**`, and both scripts carry a `/\/\*\*$/` regex literal. A line-based check
fails on both, and a check that cries wolf is a check people route around.

**Regex literals are a region too**, and they have to be. `/[/*]/` is a perfectly ordinary
way to match a slash or a star, and to a scanner that only knows strings and comments it
opens a block comment that runs to the end of the file — so it both rejects valid code *and*
hides every real comment below it, which is the worse half. `/[//]/` and `/https?:\/\//` are
the same shape: a slash inside a character class does not close the regex, and an escaped
one against the closing slash reads as `//`.

Telling a regex from a division is the one genuinely ambiguous thing in JavaScript
lexing, and this settles it the usual way: a `/` is division when the last significant
character was an identifier, a digit, `)` or `]`, unless that identifier was a keyword
(`return /x/` is a regex, `x / y` is not). Two cases stay ambiguous even in real parsers —
a regex immediately after `)` or `}`, as in `if (a) /x/.test(b)` — and this reads those as
division. The cost is a missed comment inside such a regex, never a false positive on the
code, which is the direction to fail in. `comments.exclude` in `package.json` is the way
out if a file ever hits it.

All of that is a table in `frontend/tests/devtools/comments.test.ts`, in both directions: every
comment shape is found, every slash-heavy expression is left alone, and a comment *after* a
regex holding a comment marker is still found. This is subtle code, and one example proves
neither direction — the regex handling was added because the first version shipped without
it and the table is what would have caught that.

The Python gate has the same table in `backend/tests/devtools/test_comments.py`, against its
own traps: a `#` inside a string or an f-string is not a comment, a shebang counts only on
the first line, a docstring never counts, and a file that will not tokenize is left to ruff
rather than reported here. It reads tokens instead of text for exactly the reason the
frontend's reads regexes — a gate that cannot spell a URL fragment is a gate people switch
off.

**Two carve-outs, and nothing else.** A `#!` shebang on line 1, and a TypeScript `///`
directive at the start of a line — `frontend/src/vite-env.d.ts` is one, and it is an
instruction to the compiler rather than a note to a reader. In particular there is no
carve-out for `biome-ignore`, `@ts-expect-error`, `# noqa` or `# type: ignore`, which is the
half of the rule most worth mechanising. A suppression is a threshold decision taken
silently, at the point of pain, by whoever was closest to it. Refusing the spelling forces
it to become either a fix in the code or a line in `biome.json`, `tsconfig.json` or
`pyproject.toml`, where it sits under a heading and shows up in a diff.

**What it does not cover.** Not docstrings or JSDoc, because the carve-out turns on whether
a package is consumed outside this repo and no tokenizer can answer that — both halves here
are private, so today the answer is "none of it", and that half of the rule is enforced by
review. Not JSON, TOML, YAML or Markdown: config files may carry comments where the format
offers no other way to explain a rule, which is why `pyproject.toml` and the `Makefile` are
full of them. And not `frontend/*.config.ts` — `vite.config.ts`, `vitest.config.ts` and the
two Playwright configs are configuration that happens to be written in TypeScript, and they
are outside biome's linted paths for the same reason.

A file that will not parse is skipped rather than reported. `ruff`, `tsc` and `biome` run
over the same paths in the same `make lint` invocation and fail on it first, so a syntax
error has nothing to hide behind here.

To remove it, delete the two scripts and their lines in `backend/devtools/lint.py` and the
`lint` scripts in `frontend/package.json`. Drop the *Zero comments* section from `AGENTS.md`
at the same time, or the rule goes back to being a suggestion that reads like a requirement.

## The contract artifact is excluded in four places

`frontend/src/api/schema.ts` is generated by `openapi-typescript` from `openapi.json`, and
it grows with the API — a handful of endpoints already runs to a couple of hundred lines, so
a real API clears the 500-line file limit early. It is invisible to every automatic skip:
`conformance.mjs` and `complexity.mjs` skip only `.d.ts`, `.test.ts` and `.spec.ts`, and
biome's `files.includes` lists vendored paths. So it is named explicitly in all four:

- `files.includes` in `frontend/biome.json`
- `complexity.exclude` in `frontend/package.json`
- `conformance.exclude` in `frontend/package.json`
- `comments.exclude` in `frontend/package.json`

If you add another generated or vendored file under `frontend/src/`, add it to all four at
once. Three out of four is a check that passes today and fails on the commit after next, for
reasons that will not be obvious. `openapi-typescript` writes the spec's descriptions out as
JSDoc, so the comment gate is the one that fails first and loudest.
