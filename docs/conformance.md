# Conformance

Agents write code that works and degrades. An agent decides a value at the call site rather
than once. It patches new logic into whatever function is nearest. Every step passes its tests.
These gates refuse the shortcuts that look correct in the diff and cost later, and each one
fails the build, so the standard holds without anyone remembering it. The aim is the one in
`AGENTS.md`: elegance is less code doing more, and every line must earn its keep.

## Frontend

| Category | What must hold | Enforced by |
|---|---|---|
| **Colour** | every colour resolves through the theme — no literals, no fixed palette steps, no opacity modifier on a token | `conformance.mjs` |
| **Spacing and padding** | padding, margin and gap come from the scale. Sizing is exempt — there is no token for how wide a sidebar is | `conformance.mjs` |
| **Type and fonts** | size, family, leading and tracking are declared in `@theme` and reached through a utility | `conformance.mjs` |
| **Icon weight** | one stroke token decides it, never the call site | `conformance.mjs` |
| **Data fetching** | server state comes from TanStack Query. An effect that fetches is refused; effects that subscribe, listen or touch the DOM are untouched | `conformance.mjs` |
| **Naming** | a `.tsx` file is named for what it exports, in kebab-case | `conformance.mjs` |
| **Definitions** | exported functions are declarations, not arrows assigned to a name | `conformance.mjs` |
| **Types** | strict, plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` | `tsc --noEmit` |
| **Comments** | none | `comments.mjs` |

Each of these is wrong somewhere nobody looks. A literal colour renders correctly in light
mode and keeps that same value in dark mode, where the theme never reaches it. A `useEffect`
that fetches works in the demo that made it, and skips the cache, the deduplication and the
error states the rest of the UI is written against.

Measured across bulletproof-react, documenso, shadcn-ui and excalidraw, the cost is small: the
colour rules found three instances in total, and arbitrary spacing appears in 0% to 0.6% of
component files. Sizing arbitraries outnumber spacing ones thirty to one, which is why sizing is
exempt — a rule that fires on correct Tailwind gets deleted. The opacity rule is the expensive
one, and the figure turns on directory layout rather than code quality: 1.2% to 6.1% of files
with vendored UI excluded, 4.8% to 8.9% over every file. This template earns the low end because
`components.json` pins the `ui` alias to the path `conformance.exclude` names.

## Backend

| Category | What must hold | Enforced by |
|---|---|---|
| **Correctness and simplification** | the exact simple form over the verbose one; no commented-out code | ruff `SIM` `RET` `PIE` `C4` `PERF` `ERA` |
| **Failure handling** | an exception is raised or logged, never both; `logging.exception` keeps the traceback | ruff `TRY400` `TRY401` |
| **Log messages** | a message is a format string with arguments, not an f-string already collapsed to text | ruff `LOG` `G` |
| **Types** | strict, no untyped seam | basedpyright |
| **Prose** | spelling in names, docstrings and messages | codespell |
| **Comments** | none | `comments.py` |

There is no design-system analogue on this half. Those rules gate a Tailwind theme, a React hook
and a query cache; a Python counterpart would be a check nobody measured.

## Complexity

Both halves, and the reason they are gated at all:
[SlopCodeBench](https://arxiv.org/pdf/2603.24755) measured 15 agents and found structural
erosion rising in **77% of trajectories** — new logic patched into functions that are already
complicated rather than distributed into focused ones.

**A single threshold cannot catch that.** A per-function limit misses a codebase where
everything creeps to just under the line. An aggregate misses one catastrophic function among
many small ones. A ratchet against yesterday misses drift accepted one approved increase at a
time. So each half runs several.

| Category | frontend | backend |
|---|---|---|
| **Per function** | cognitive complexity, biome, in the editor | cyclomatic complexity, ruff `C901` |
| **Function volume** | lines, biome | statements, ruff `PLR0915` |
| **File volume** | a backstop over `src/**` | none — service modules grow by adding routes |
| **Drift** | density against a committed baseline | mean against a committed baseline |
| **Ceiling** | relative, a multiple of the project's origin | absolute |

Drift is **two-sided**. Whatever the tree improved since the baseline was recorded is slack, and
slack is spendable by the next commit — so a figure below the baseline by more than the
tolerance means the baseline is stale. `make lint` lowers it for you; `make lint-check` refuses
and says so. A rise stays manual and lands in a diff, because someone is consenting to more
complexity.

**When a gate fires, split the function.** Raising a threshold or re-recording a baseline upward
is a decision that belongs in a diff, not the way to make a build green — and the zero-comments
rule bans `biome-ignore`, `# noqa` and `# type: ignore`, so there is no quiet way past the
per-function gates either.

### Where the numbers came from

Measured rather than picked, so changing one is an argument with evidence.

**Frontend**, against thirteen well-regarded codebases using biome's own counter, so the number
in the editor, in `pnpm lint` and in the gate is one number. Four React apps — including
bulletproof-react and excalidraw — and nine TypeScript libraries including hono, zod and vitest.
The per-function cap flags 0.00–5.59% of functions across them, with React at the comfortable
end because JSX adds structure without branching. Length is capped separately because it is
independent of complexity: sixty lines of markup score **0**. The file cap is a backstop at two
and a half times the largest file in bulletproof-react, which has no file over 200 lines across
104. The drift tolerance leaves roughly 2.4× headroom over the largest single-commit rise in 200
commits of hono (+0.82%) and zod (+0.60%).

**Backend**, against httpx and flask with ruff's cyclomatic counter: p95 is 6 and p99 is 9–11,
so the cap flags roughly the top 2%.

**The ceilings differ because the distributions do.** Mean cyclomatic complexity sits at 2.2 and
2.3 across httpx and flask — tight enough for an absolute number. No aggregate cognitive metric
does: density runs 21.8 (remeda) to 110.9 (vitest), while within one project it barely moves,
hono going 101.6→104.5 across 200 commits. So the frontend anchors to where the project started.

**Never blend the two into one number.** Different instruments, different units, different
bodies of code. Cognitive complexity has a floor of 0 and a fat tail; cyclomatic has a floor of 1
and a tight one. Fifty lines and twenty-five statements do not convert.

## Security

Every row carries a `tenant_id`, and a **forced** row-level security policy enforces it in the
database. A query that forgets to filter still cannot cross a tenant — the isolation does not
depend on application code being correct.

| Category | What must hold | Enforced by |
|---|---|---|
| **Isolation is forced, not merely enabled** | the role that owns a table is still bound by its own policy, so no connection sits above it | `test_isolation.py`, against a real server; `adr/0002` for why |
| **A tenant touches only its own rows** | read, write and delete are all covered, and `WITH CHECK` stops a tenant inserting what it could not then read | `test_isolation.py` |
| **An absent tenant is not a wildcard** | an empty or missing setting matches nothing, and cannot be stored either | `test_isolation.py` |
| **The tenant does not outlive its request** | a pooled connection cannot carry one request's tenant into the next | `test_isolation.py` |
| **The application role can do nothing but DML** | no `BYPASSRLS`, no `CREATE`, no writing the ledger, and no `SECURITY DEFINER` function to borrow rights from | `test_isolation.py` |
| **Ownership stays with the owner role** | an object owned by anyone else escapes the policy set | `test_isolation.py` |
| **Every index leads with `tenant_id`** | otherwise the policy predicate cannot be satisfied and Postgres scans — right answers, quietly slower | `test_schema.py` |
| **One door to a store, and one to the substrate** | `Database.store(tenant_id)` is called in exactly one place, and the substrate is taken off the app in exactly one place, so no route can hold an unscoped store or ask for another tenant's | `test_tenant_scoping.py` |
| **No route escapes the identity seam** | every route the app declares resolves a tenant before its handler runs, and the exempt ones are named in a list checked in both directions | `test_guarantee.py`, which reads the routes off the app and drives each one against a seam that refuses; `adr/0008` for why |
| **The application never holds owner rights** | it refuses to start if it can see `DATABASE_OWNER_URL` | `wiring.py`, at boot |

Two things carry the design. `Database.store(tenant_id)` is the only way to obtain a store and
the tenant is a parameter on no method, so a store you can hold is a store already scoped. And
`app/identity.py` is the single place that decides who a request is; it ships as a stub that
authenticates nothing and returns the sentinel tenant, under one rule that outlives the stub:
**an unresolvable credential is a refusal, never an anonymous principal.** A missing, expired or
malformed credential that quietly falls back to the sentinel hands one tenant's data to anybody
who failed to log in.

That rule is wired rather than asked for. Every route reaches the seam through one dependency,
so replacing the stub is a change in one place instead of an audit of every handler, and a
refusal raised there answers `401` with `WWW-Authenticate` even if the replacement registered no
handler of its own. Until the stub is replaced, every boot states that the deployment
authenticates nothing, and `UNAUTHENTICATED_IS_INTENTIONAL` is how a deployment that means it
records so and reads that line as `INFO` instead.

A superuser bypasses every policy whatever `FORCE` says, which is why the application connects
as a least-privilege role and the suites do too — a test wired to the admin connection would
pass against a database with no isolation in force.

## Architecture

Not lint rules — tests that fail when the **shape** of the system changes. Each exists because
the failure it catches otherwise looks like working software.

| Category | What must hold | Enforced by |
|---|---|---|
| **Contract freshness** | the committed spec and generated types match the backend code | `make openapi-check`, in the gate; `adr/0007` for the settings it depends on |
| **Two implementations, one contract** | the [MSW](https://mswjs.io/) handlers and the real backend answer alike | one contract suite, run against both |
| **Typed mocks** | a handler cannot return a status code or shape the spec does not declare | openapi-msw, at compile time |
| **Mock mode leaves no trace** | a production build ships no worker and no msw bundle | a build assertion in the gate |
| **Two substrates, one store** | in-memory and Postgres satisfy the same suite | `store_contract.py`, run twice |
| **Schema integrity** | one statement per key, in applied order, with a later column also in its `CREATE` | `test_schema.py` |
| **One-origin serving** | the built bundle answers deep links and refuses a stale hashed asset | `test_serve.py`, and the gate against a real build |
| **Test discipline** | no test switches itself off; the tiers stay out of the gate | `test_gate.py`, `tiers.py`; `adr/0005` for why |
| **Gate discipline** | the hook runs the gate, says when it ran only part, and offers no way off | `test_gate.py` |
| **Document reachability** | every document a file names exists, and every document is named by another file | `links.py`, over this repository and over a generated project; `links_test.py` holds it to each citation spelling |

Both directions of document reachability matter, and the second is the quiet one. A deleted
document leaves its readers naming nothing, which is noisy; it also takes the only pointers to
whatever it linked with it, and an unfindable decision gets made again instead of read.

A sweep that recognises nothing finds nothing, reports nothing and exits green, so `links_test.py`
runs first and fails the gate when a citation spelling stops being recognised. Each of its cases
was written by breaking the thing it covers and watching the case fail.

The fixture file is the one thing the sweep skips, because the paths in it are inputs: every dead
link it names is one it exists to prove gets caught.

That check still cannot see everything, so do not read a green run as more than it is. An anchor
goes unchecked, so a heading can move. A path built from a shell variable goes unchecked. A file
holding a null byte is reported and read for no names at all, while a file that is malformed
UTF-8 is reported and read with the bad bytes replaced — so a name that ran through one of them
is missed. And a name in this repository can be answered by a file that exists only under
`template/`, because a gate script here legitimately names paths that exist only once a project
is generated.

Mock mode is why several of these exist. A frontend that runs with no backend is worth having, and
it is also the fastest way to build an application that only works against a fiction: the
handlers drift, the app stays green, and the gap appears on deploy. Typing the handlers from the
spec and running one contract suite against both implementations is what keeps the fake honest.

## Conformance fails closed; the agent guard fails open

The cost of a false positive decides it. A guard false positive blocks a command an agent needs
and burns an afternoon with no cheap way past, so the guard allows what it is unsure of. A
conformance false positive blocks a commit and costs one reviewable line of config, so the
checks refuse what they are unsure of — and that escape hatch is what makes failing closed
affordable, because a genuine exception lands in the diff where it gets reviewed.
