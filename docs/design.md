# Design

Why this template is shaped the way it is. The decisions here were settled before the repo
existed and validated by a prototype that ran the whole contract pipeline end to end; the
findings that prototype produced are recorded below because three of them changed the
design. Individual decisions that are hard to reverse also have an ADR in `docs/adr/`.

## What this is

A third sibling to [agent-ready-ts](https://github.com/CodeMocsh/agent-ready-ts) (React SPA
generator) and [agent-ready-python](https://github.com/CodeMocsh/agent-ready-python) (Python
library generator). It generates a **monorepo application**: a TypeScript React frontend
talking to a Python FastAPI backend, with the agent-ready layer both siblings ship.

The two siblings generate things meant to be *consumed* — an SPA, a library. This one
generates a **system**: two halves that must agree with each other. That single difference
drives every design decision that follows, and it is why this template ships a contract and
a test that proves the halves interoperate.

The TS sibling's own `template/docs/development.md` already names the destination:

> If the backend publishes an OpenAPI spec, the next step is `openapi-typescript` plus
> `openapi-msw`, which type the handlers against the spec so a backend change breaks the
> build instead of surprising you at runtime.

This template is that next step, made default.

---

## Verified by the spike

Before any of this repo existed, a throwaway prototype built a real FastAPI backend, a real
pnpm frontend, the full contract pipeline and the dual-run contract suite. Results below are
measured, not reasoned about.

### The pipeline composes

```
FastAPI + pydantic  →  openapi.json (3.1.0)  →  schema.ts  →  types.ts + openapi-msw handlers
```
Backend `pytest` 5/5. Frontend `tsc --noEmit` clean. Contract suite **5/5 against MSW
handlers and 5/5 against the live backend through the vite proxy**. Export is
byte-deterministic across runs; a model edit without regeneration is caught by comparison.

### Versions that resolved under the 14-day cool-off (2026-08-16)

| Half | Installed |
|---|---|
| backend | fastapi 0.141.1, pydantic 2.13.4, uvicorn 0.52.1, starlette 1.3.1, pytest 9.1.1, httpx 0.28.1 |
| frontend | msw 2.15.0, openapi-msw 2.0.0, openapi-typescript 7.13.0, typescript 7.0.2, vite 8.2.0, vitest 4.1.10, @types/node 24.13.3 |

`pnpm install` succeeded with the sibling's `pnpm-workspace.yaml` verbatim
(`minimumReleaseAge: 20160`, `trustPolicy: no-downgrade`, `trustPolicyExclude: ['semver@6.3.1']`,
`allowBuilds: msw`), so those floors are safe to ship today.

### Finding 1 — openapi-typescript does not work with TypeScript 7 *(load-bearing)*

The TS sibling pins `typescript ~7.0.2`. `openapi-typescript@7.13.0` declares
`peerDependencies: { typescript: "^5.x" }` and **crashes** under TS 7:

```
TypeError: Cannot read properties of undefined (reading 'createKeywordTypeNode')
  at .../openapi-typescript/dist/lib/ts.mjs:11
```

TypeScript 7 is the native port; `ts.factory` — the compiler API openapi-typescript builds
its AST with — is gone. **pnpm only warns**, so this fails at run time, not install time.

The fix that keeps the frontend on TS 7 (sibling parity): run the generator isolated, with
its own TypeScript, pinned exactly:

```jsonc
"openapi:types": "pnpm dlx openapi-typescript@7.13.0 ../openapi.json -o src/api/schema.ts"
```

Measured at 0.9s warm. The trade is explicit and belongs in an ADR: the generator's version
lives in a script string rather than the lockfile, so **pin it exactly and bump it
deliberately** — a floating version would silently rewrite a contract artifact. The
alternative (drop the frontend to TypeScript 5) buys lockfile coverage at the cost of
diverging from the TS sibling on a language major; prefer `dlx` until openapi-typescript
supports TS 7, then collapse it back into a devDependency.

### Finding 2 — a declared 404 with no `model` makes the spec lie *(load-bearing)*

`responses={404: {"description": "Task not found"}}` declares **no response body**, but
FastAPI's `HTTPException` returns `{"detail": "..."}`. openapi-msw caught it as a type error
(`Object is of type 'unknown'` on `response(404).json(...)`) — the honesty mechanism working
before a single test ran. Every error response needs a model:

```python
class ErrorBody(BaseModel):
    detail: str

NOT_FOUND = {404: {"description": "Task not found", "model": ErrorBody}}
```

### Finding 3 — the trailing-slash 307 escapes the proxy *(load-bearing)*

FastAPI redirects `/tasks/` → `/tasks` with a 307. Through the vite proxy that redirect
carries the **backend's own origin**:

```
GET http://localhost:5173/api/tasks/  →  307   location: http://localhost:8000/tasks
```

In a browser that becomes a cross-origin request to an origin serving no CORS headers — a
confusing failure far from its cause, and it leaks internal topology. The fix, verified to
turn it into a plain 404 while leaving the spec byte-identical (it is behavior, not
contract):

```python
app.router.redirect_slashes = False
```

### Finding 4 — `.python-version` is load-bearing

With `requires-python = ">=3.12"` and no `.python-version`, uv resolved the environment to
the system **Python 3.14.7**. The pin file is what makes CI and laptops agree.

### Smaller confirmations

- **OpenAPI 3.1.0** is emitted; `separate_input_output_schemas=False` yields exactly
  `Task`, `CreateTaskBody`, `UpdateTaskBody` — the names the TS sibling already hand-wrote,
  so `types.ts` becomes three aliases and every consumer compiles unchanged.
- **Auto-422s are real and include DELETE** (any route with a path parameter), adding
  `HTTPValidationError` + `ValidationError` to `components.schemas`. Expected; never "clean"
  that diff.
- **Type enforcement bites** on all three sabotage cases: an undeclared status code, a wrong
  response body shape, and a path absent from the spec each fail `tsc`.
- **TypeScript 7 removed `baseUrl`** — `tsconfig.json` must express the `@/` alias with
  `paths` alone. The sibling already complies; do not reintroduce it.
- **Starlette deprecation**: `TestClient` warns that using `httpx` is deprecated in favour of
  `httpx2`. Cosmetic today; watch it when pinning the backend dev group.
- **The contract suite must be state-agnostic.** Mock mode resets to seed data after each
  test, live mode does not. Assertions cover shapes and status codes, never seed contents —
  which is also the design's stated position: seeds are convenience, the spec is the contract.
- **Backgrounded servers hold stdout open**, so a naive `sh dev.sh | tail` never sees EOF
  even after the script exits. Resolved by supervision rather than redirection: `dev.sh`
  outlives its children and kills them on the way out, so the last writer closes the pipe.
  A CI step that boots the halves and returns must still redirect, and
  `devtools/contract-test.sh` does.
- **`exec` discards traps.** The first `dev.sh` handed the terminal to vite with
  `exec pnpm dev:live`, which replaced the shell and with it the cleanup trap, so Ctrl-C
  stopped the frontend and left uvicorn holding :8000. The frontend still runs in the
  foreground — that is what keeps vite's shortcuts — but without `exec`, so the shell is
  there to clean up.
- **Signalling that script is not the same as pressing the key.** While vite holds the
  foreground, a `kill` to `dev.sh` cannot be handled until vite returns, so a test built on
  one would deadlock and prove nothing. `check_template.sh` forks a pty and writes `0x03`,
  which is what the terminal's line discipline turns into a SIGINT for the foreground
  group. Nothing about this failure is visible in a diff, and it had already shipped once.

---

## Decisions

### 1. Generator: pure Copier (→ ADR-0001)

`copier.yml` + `_subdirectory: template`, zero generator code — the Python sibling's
mechanics, not the TS sibling's bespoke CLI. Three reasons, in order of weight:

1. **The npm dotfile trap disappears.** `pnpm dlx github:` runs the template through npm's
   pack/install machinery, which renames a packaged `.gitignore` to `.npmignore` and drops
   nested ones entirely. This tree is the most dotfile-heavy of the three. Copier delivers by
   `git clone`; the trap, the `restoreDottedName` workaround, and the pack round-trip check
   all vanish.
2. **`copier update` is free.** The TS sibling documents having no update path as tracked
   debt. This template will evolve fastest of the three (a contract flow, two ecosystems).
3. **The toolchain objection dissolves.** Requiring `uvx` to generate a project whose backend
   requires uv is not a cost.

Distribution: `uvx --exclude-newer "14 days" copier@9.16.0 copy gh:CodeMocsh/agent-ready-fullstack-ts-python my-app`.
Works against a private repo (git supplies credentials). Tag `v0.x.y` from day one —
`copier update` resolves against tags.

**Hard rules.** Workflow files are never `.jinja`, so `${{ }}` never meets Jinja. `.ts`,
`.tsx`, and `.py` source files are never `.jinja`. The backend is **entirely token-free** —
its only `.jinja` is `pyproject.toml.jinja` — because anything project-specific in
`app/main.py` would leak into `openapi.json`, a committed artifact sworn never to be
hand-edited. The demo API is titled `"Tasks API"` in every generated project, and rebranding
happens when the user replaces the demo and regenerates in the same commit.

### 2. Questions — six, exactly the TS sibling's

| Question | Default | Validation |
|---|---|---|
| `package_name` | `changeme` | `^[a-z][a-z0-9]*(-[a-z0-9]+)*$` |
| `package_description` | `changeme` | — |
| `package_author_name` | `changeme` | — |
| `package_author_email` | `changeme@example.com` | — |
| `package_github_org` | `changeme` | — |
| `package_license` | `MIT` | MIT / Apache-2.0 / BSD-3-Clause / AGPL-3.0-or-later / Proprietary / None |

Strict kebab-case is the intersection of npm, PEP 503, GitHub repo, and directory naming —
stricter than either sibling, on purpose, because this one name has to satisfy all four.
Computed with `when: false`: `package_license_spdx` (`None`/`Proprietary` → `UNLICENSED`).

**Dropped**: `publish_to_pypi` (this generates an application) and `package_module` (the
backend package is fixed as `app`). Defaults must stay behavior-preserving so
`copier update --defaults` is a no-op — copy that comment from the Python sibling.

Conditional files use Jinja filenames — `{% if package_license != 'None' %}LICENSE{% endif %}.jinja`
— and license bodies use the single if/elif chain from the Python sibling. No `.if-license`
suffix and no `licenses/` directory: both exist only because the bespoke renderer lacks the
expression language Copier already has.

### 3. Layout: two halves, each shaped by its own ecosystem

```
<generated>/
├── .copier-answers.yml   AGENTS.md   CLAUDE.md   README.md   LICENSE?
├── Makefile              openapi.json          ← contract artifact, committed, generated
├── .gitignore            .claude/  .entire/  .githooks/  devtools/  docs/
├── .github/workflows/ci.yml
├── frontend/             ← the TS half: everything pnpm touches
│   ├── package.json  pnpm-workspace.yaml  pnpm-lock.yaml  tsconfig.json  biome.json
│   ├── vite.config.ts  vitest.config.ts  playwright.config.ts  components.json  index.html
│   ├── .env.development  .env.mock  .env.live
│   ├── devtools/complexity.mjs   .complexity-baseline.json
│   ├── public/{favicon.svg, mockServiceWorker.js}
│   ├── e2e/{tasks.spec.ts, tasks.live.spec.ts}
│   ├── tests/{setup.ts, render.tsx, task-list.test.tsx, contract.test.ts}
│   └── src/
│       ├── api/{base.ts, client.ts, types.ts, schema.ts}   ← schema.ts is generated
│       ├── mocks/{handlers.ts, store.ts, browser.ts, node.ts}
│       ├── components/{task-list.tsx, ui/*}   lib/utils.ts
│       └── main.tsx  router.tsx  index.css  vite-env.d.ts
└── backend/              ← the Python half: everything uv touches
    ├── pyproject.toml  uv.lock  .python-version
    ├── app/{__init__,main,models,routes,store}.py
    ├── tests/test_tasks.py
    └── devtools/{lint.py, complexity.py, export_openapi.py}  .complexity-baseline.json
```

`frontend/` and `backend/` are **halves** — toolchain boundaries, each independently
installable, lintable, testable, buildable without the other. `src/` and `app/` are the
source directories *inside* them: `frontend/src/` is to `backend/app/` as pnpm is to uv.
Naming the Python package `app` (rather than the project) keeps `uvicorn app.main:app` — the
invocation in every FastAPI reference — and removes a question from the generator.

`apps/web` + `apps/api` was rejected: that shape advertises a many-package workspace with
shared `packages/*`, and pnpm's workspace machinery cannot span into Python anyway. There is
no root `package.json`; `pnpm-workspace.yaml` stays in `frontend/` as the settings carrier it
is in the sibling.

### 4. The contract (→ ADR-0002)

**Code-first.** pydantic models and FastAPI route declarations are the single authoring
point; everything downstream is derived and committed:

```
backend/app/{models,routes}.py   →   /openapi.json   →   frontend/src/api/schema.ts
                                                          ├→ types.ts (three aliases)
                                                          └→ mocks/handlers.ts (openapi-msw)
```

A hand-authored spec was rejected: FastAPI's decorators are executable and pytest exercises
them, while a YAML file can lie in ways nothing runs.

Both derived files are **committed**, which is what lets each half stay independently
operable — the frontend regenerates types, tests, and builds with no Python installed. That
is mock mode's guarantee restated at repo level.

```make
openapi:
	cd backend && uv run python devtools/export_openapi.py ../openapi.json
	pnpm -C frontend openapi:types

openapi-check: openapi
	@git diff --exit-code openapi.json frontend/src/api/schema.ts \
	  || { echo "contract artifacts are stale -- run 'make openapi' and commit the result" >&2; exit 1; }
```

Export runs in-process (`app.openapi()`), never booting a server; it needs a `sys.path`
insert because `package = false` installs nothing. CI splits the assertion so neither job
installs the other toolchain: the **backend job** proves spec↔code, the **frontend job**
proves types↔spec, and transitively code↔types.

Required backend settings, all three verified by the spike:
`FastAPI(title="Tasks API", separate_input_output_schemas=False)`,
`app.router.redirect_slashes = False`, and a `model` on every declared error response.

The `/api` prefix is **deployment topology, not contract**: the backend serves bare `/tasks`,
the vite dev server proxies `/api` → `:8000` with the prefix rewritten, and the frontend's
`src/api/base.ts` stays byte-identical to the sibling. Because the browser sees one origin,
the backend ships **no CORS middleware at all**; `docs/development.md` carries the
`CORSMiddleware` snippet for anyone who deliberately points `VITE_API_BASE_URL` cross-origin.

### 5. Validation ladder

| Level | Mechanism | Proves |
|---|---|---|
| 0 | `check_template.sh` static assertions | the generator renders the right files |
| 1 | vitest (MSW) + pytest (TestClient) | each half works alone |
| 2 | `make openapi-check` | committed artifacts match backend code |
| 3 | openapi-msw + `tsc --noEmit` | handlers conform to the spec at compile time |
| **4** | **`frontend/tests/contract.test.ts`, run twice** | **the halves interoperate** |

**Level 4 is the fullstack-specific addition and the reason this repo exists.** Levels 0–3
all pass green on a project whose frontend cannot reach its backend at all. One vitest file
exercises CRUD through the real `tasksApi` client and runs twice with identical assertions:

- **Run A** — MSW node server intercepts (`pnpm test`, today's setup).
- **Run B** — `CONTRACT_TARGET=live`, client pointed at `http://localhost:5173/api`, uvicorn
  behind the vite dev server. One pass traverses the proxy, the rewrite, real HTTP, real
  JSON, and the real store.

This is the standard "same suite against the fake and the real implementation" pattern for
keeping a test double honest. A separate shell smoke script would prove a strict subset, so
none ships. Only Level 4 catches: the 307 trailing-slash trap, a missing proxy `rewrite`,
port constants drifting between `vite.config.ts` and `dev.sh`, wire shapes TestClient never
sees, and 404/204 divergence between `store.py` and `store.ts`.

**Where it runs**: root `make test`; the generated project's pre-commit hook (both toolchains
present on a real dev machine, graceful skip when either is missing); and the generator's own
`check_template.sh`, which is the enforced gate proving the template itself works. The
generated project's CI stays at two lightweight jobs, with the opt-in third job written out
in `docs/development.md` as prose rather than commented-out YAML.

**Not in v1**: schemathesis property-based conformance — on-brand, but a template that flakes
on day one gets deleted. Documented as the natural next step.
**Shipped**: `e2e/tasks.live.spec.ts`, a Playwright live-mode spec, out of CI exactly like the
existing mock e2e, as the agent's "see what you built" loop against a real backend.

### 6. Root Makefile and dev orchestration

Targets: `install`, `hooks`, `lint`, `lint-check`, `test` (both halves **and** the contract
suite), `test-fast` (per-half only), `dev`, `dev-frontend`, `dev-backend`, `openapi`,
`openapi-check`, `build`, `upgrade`, `clean`. Exports `UV_EXCLUDE_NEWER ?= 14 days`.

`make dev` means **live mode, both halves** — at monorepo altitude "dev" means the system,
and mock-only stays one command away as `make dev-frontend`. `devtools/dev.sh` is ~40 lines
of POSIX sh: uvicorn backgrounded on :8000 in a process group of its own, vite on :5173 in
the foreground so it keeps the terminal, and one `trap` that outlives them both. Not
`exec`ed — see the finding above. A backend that never answers is surfaced rather than left
to look like a frontend bug.

Cleanup escalates — SIGTERM, one second, SIGKILL — because uvicorn's `--reload` supervisor
does not reliably exit once its worker is gone and `uv run` waits for it. A lone TERM leaves
the script blocked in `wait` forever, so Ctrl-C never returns the prompt: the original bug
wearing a different hat, and observed once across four full runs before the escalation went
in. Keeping vite in the foreground is what preserves its keyboard shortcuts, and the price
is that a signal sent to the script alone waits for vite to return — which is why
`check_template.sh` drives a pty and writes `0x03` rather than calling `kill`. No
`concurrently`, no honcho; two terminals remain the documented fallback.

`make upgrade` **must end with `make openapi`**: `app.openapi()` is deterministic for pinned
versions but not across FastAPI/pydantic minor bumps, and `openapi-typescript` output changes
with its own version.

### 7. CI for the generated project

One workflow, three jobs. **frontend**: pnpm frozen install → `lint:check` → `test` →
types-sync assertion → `build`. **backend**: `uv sync --frozen` → `devtools/lint.py --check`
→ `pytest` → spec-sync assertion. **contract**: both toolchains → `make test-contract`.
SHA-pinned actions, `persist-credentials: false`, `UV_EXCLUDE_NEWER: "14 days"` at workflow
env.

The third job breaks the single-toolchain rule on purpose, and it is the only one that can
fail on the halves not interoperating — the other two pass on a project whose frontend cannot
reach its backend at all. It was originally left to the pre-commit hook, which was wrong: the
hook skips itself when a clone has installed only one half, so the check had a caller but no
gate.

**No version matrix** — Node 24, Python 3.12, the runtimes this app deploys on. Both siblings
matrix because they generate broadly-consumed artifacts; an application tests what it runs.
Playwright stays out, reproducing the sibling's documented trade. CI never deploys.

### 8. Agent-ready layer — one root layer, one guard

A single root `.claude/`, `.entire/`, and `AGENTS.md`. Both Claude Code and the AGENTS.md
convention resolve upward from cwd, so one layer covers work in either half; two would drift,
and two Entire configs would double-checkpoint one session.

The one guard is **`agent_guard.py`**, verbatim from agent-ready-python's `origin/main`. The
guard's value is highest at minute zero on a fresh clone, before any install has run, so the
tiebreaker is which interpreter is already present: python3 ships with macOS CLT and every
Linux and CI image; Node ships with nothing. The wrapper already fails open when python3 is
missing. Shipping both guards was rejected — two rule sets to keep in sync is exactly the
drift this family exists to prevent.

The shared prose survived the new commits intact — `a21c56d`/`a82ef37` touched only the
frontend conventions and one pointer sentence — so the word-for-word plan still holds. Their
new material (Styling with enforcement named, and a **Data** bullet banning fetches in
`useEffect` in favour of TanStack Query hooks) lands in `### Frontend`.

Root `AGENTS.md` order: **Approach** and **Zero comments** word-for-word from the siblings
(including the newest line, *"Never swallow an error or return a silent default in its
place"*, with the suppression list naming both ecosystems: `biome-ignore`, `@ts-expect-error`,
`# noqa`, `# type: ignore`); **Vendored and generated code** (shadcn and
`mockServiceWorker.js` are yours to edit behaviorally, `openapi.json` and `schema.ts` are
regenerate-only); **The contract** (the flow, `make openapi`, the auto-422 note, and
regenerate-never-hand-merge for conflicts); **Complexity** (both ratchets); **See what you
built**; then `### Frontend`, `### Backend`, `### Both halves`.

### 9. Quality gates — per half, and asymmetric on purpose

The TS sibling landed three waves of gating after this design began (`1de31c8`, `a21c56d`,
`a82ef37`). All of it is frontend material; agent-ready-python stands still at `f8d5591`.
The resulting split is not an oversight to reconcile — the sibling's own `updating.md` now
records it — and it maps cleanly onto two halves:

| Gate | frontend | backend |
|---|---|---|
| per-function complexity | biome `noExcessiveCognitiveComplexity` 15 | ruff `C901` at 8 |
| function length | biome `noExcessiveLinesPerFunction` 50 **lines** | ruff `PLR0915` at 25 **statements** |
| file length | biome `noExcessiveLinesPerFile` 500, `overrides` → `src/**` | none |
| verbosity | `useSimplifiedLogicExpression`, `noUselessElse`, `useCollapsedElseIf`, `noNegationElse` | ruff SIM/RET/PIE/C4/PERF/ERA |
| density ratchet | `devtools/complexity.mjs` + `.complexity-baseline.json` | `devtools/complexity.py` + baseline |
| design-system conformance | `devtools/conformance.mjs` (8 checks) | none — no analogue exists |

Never blend the two halves into one number: React applications and Python services sit in
different density bands, the units differ (lines vs statements), and every threshold was
measured on its own side. The sibling states the rule plainly — *"Both exist, neither number
carries across… Do not reconcile them."*

**Conformance is frontend-only by construction.** `conformance.mjs` gates a Tailwind theme, a
React hook and a query cache; there is no Python analogue, and the sibling says so. What
ports is the *selection rule*, not the rules: gate what renders correctly on the screen the
agent is looking at and is wrong on one it never opens; leave anything an agent can verify
from its own diff to `AGENTS.md`. Its eight checks are the six line-by-line patterns
`raw-colour`, `palette-utility`, `named-colour`, `arbitrary-spacing`, `arbitrary-type` and
`raw-type-declaration`, plus the two structural ones `effect-data` and
`inline-type-declaration`, configured by a `conformance` block (`themeFiles`, `allow`,
`exclude`) and chained into `lint`/`lint:check`.
Note it **fails closed**, unlike the agent guard, with `conformance.allow` as the reviewable
escape hatch — the asymmetry follows the cost of a false positive.

**File-length gating reverses an inherited position.** Earlier design drafts carried the
Python sibling's "deliberately no file-length limit" argument. That still holds for the
backend; the frontend now gates at 500 non-blank lines across `src/**` as a backstop, with
the number measured against React codebases. Carry both, per half, and record the divergence.

#### `frontend/src/api/schema.ts` must be excluded in three places

This is the one place the new gates collide with this template's own design, and it would
have shipped broken. The generated contract artifact is a plain `.ts` file that grows with
the API — the spike's was 224 lines for four endpoints, so a real API clears the 500-line
file limit early. It is invisible to every automatic skip: `conformance.mjs` and
`complexity.mjs` only skip `.d.ts`, `.test.ts` and `.spec.ts`, and biome's `files.includes`
lists vendored paths only. So `src/api/schema.ts` must be added to **all three** exclusion
surfaces:

```jsonc
// frontend/biome.json
"files": { "includes": ["**", "!dist/**", "!node_modules/**",
                        "!public/mockServiceWorker.js",
                        "!src/components/ui/**", "!src/lib/utils.ts",
                        "!src/api/schema.ts"] }

// frontend/package.json
"complexity":  { "exclude": ["src/components/ui/**", "src/lib/utils.ts", "src/api/schema.ts"] },
"conformance": { "themeFiles": ["src/index.css"], "allow": [],
                 "exclude": ["src/components/ui/**", "src/lib/utils.ts", "src/api/schema.ts"] }
```

The sibling's check script already asserts that each `exclude` list contains the vendored
paths; ours extends that assertion to the contract artifact, in all three lists. (Emitting
the file as `schema.d.ts` would earn two of the three skips automatically, but it still needs
the biome entry and it obscures that the file is a normal module — prefer the explicit
exclusions, which are what the check script can assert.)

Also record: the Python ratchet is inert without a committed baseline (prints a notice and
passes) while the TS one fails on crossing into range with no baseline — prefer the TS
posture on both halves if the port is cheap. And the TS `updating.md` density figures are
stale (pre-metric-redefinition); quote `docs/agent-tooling.md`'s numbers instead.

### 10. Supply chain

Frontend: `frontend/pnpm-workspace.yaml` verbatim from the sibling. Backend:
`exclude-newer = "14 days"` in `[tool.uv]`, `UV_EXCLUDE_NEWER` exported by the root Makefile,
and the same value in CI env — three enforcement points, because `uvx` and tool invocations
run outside project discovery.

**State the asymmetry rather than papering over it**: pnpm additionally enforces
publish-evidence `no-downgrade` and blocks install scripts; uv has no trust-evidence check at
all, so `exclude-newer` is the only mechanical gate on a PyPI dependency. Scrutiny when adding
a Python package substitutes for a check that does not exist. `docs/development.md` says this
in as many words.

### 11. Sync across three repos

The fullstack repo is a **pure downstream consumer** and never originates a shared-layer
change. A fix discovered here is PR'd to the repo that owns the file — `agent_guard.py` to
the Python sibling, `agent-guard.mjs` to the TS one, language-neutral files
(`install-hooks.sh`, the Entire wiring, the shared AGENTS.md prose, the guard tables, the
hook-activation suite) to agent-ready-python as tie-breaker — and pulled back. The agent-facing
rule is one line: *in this repo, shared-layer files are read-only; upstream the fix.*

Re-pointing the flow through the newest, least-proven repo would maximize churn; the siblings'
pairwise contract already works. `updating.md` carries the in-step list, the manual
`diff`/`sed` drift commands, and the per-repo exclusions (stacks, complexity thresholds,
variant lists, the contract flow, generator mechanics). Scaffolding this repo obligates two
small follow-up PRs adding a pointer paragraph to each sibling's `updating.md`.

### 12. Generator repo skeleton

```
agent-ready-fullstack-ts-python/
├── copier.yml            template/            devtools/{check_template.sh, install-hooks.sh}
├── .githooks/pre-commit  Makefile             .github/workflows/ci.yml
├── AGENTS.md  CLAUDE.md  CONTEXT.md  updating.md  README.md  LICENSE
├── docs/adr/{0001,0002,0003}-*.md
└── .claude/  .entire/    ← dogfooded WITHOUT the agent guard, as both siblings do
```

No `src/`, no `package.json`, no `licenses/`, and no `unit` CI job — Copier removes the
generator code those exist to serve. CI is a three-variant matrix
(`default` / `proprietary` / `no-license`) over `check_template.sh`. Pre-commit runs the full
default variant, matching both siblings; if the fullstack full run proves painful, dropping
the hook to `FAST=1` and leaving CI on the full run is the sanctioned fallback — a decision to
make in the open, not silently.

### 13. `check_template.sh` outline

Base it on agent-ready-python's `origin/main` version (tool preflight, `GIT_*` unsetting, tar
staged through a file because CI's dash has no `pipefail`), then:

1. Preflight `uvx uv tar python3 git node pnpm`; `UV_EXCLUDE_NEWER="14 days"`; `COPIER_SPEC="copier@9.16.0"`.
2. Variant case; working-tree tar copy; `copier copy --defaults --data …`.
3. Agent-ready layer: `AGENTS.md`, `CLAUDE.md` contains `@AGENTS.md`, `.claude/settings.json`
   parses, guard `py_compile`s, `.entire/settings.json`, `docs/agent-tooling.md`,
   `.copier-answers.yml` has `_src_path` and `_commit`, `test -x` on the hooks and scripts.
4. Copier-specific safety net: no surviving `{{ … }}` (excluding `${{ }}`), no surviving `{%`,
   and `find . -name '*.jinja'` empty — the forgotten-suffix failure mode.
5. **Adversarial render** (default variant only): a second render whose free-form answers
   carry quotes, backslashes, ampersands and angle brackets, with both manifests parsed back
   and every answer compared against what went in. Interpolating an answer raw into
   hand-written JSON or TOML is the failure that lands in someone else's project, on
   `pnpm install`, with nothing in the diff to suggest why.
6. Guard deny/allow tables — the union of both siblings', plus `rm -rf frontend/node_modules`
   and `rm -rf backend/.venv` in MUST_ALLOW. Mind that Python's `json.dumps` emits
   `"permissionDecision": "deny"` **with** a space.
7. App shape, both halves; contract artifacts exist; the generated workflow's contract job
   and its two easy-to-omit setup inputs; supply-chain greps (`minimumReleaseAge`,
   `trustPolicy`, `exclude-newer` ×3, actions SHA-pinned), every policy exclusion pinned to
   an exact version, and every runtime dependency bounded above; complexity-ratchet
   assertions ×2 halves; the conformance block (frontend only); the per-file line limit; and
   the three-list exclusion assertion extended to `src/api/schema.ts`.
8. Hook-activation suite, verbatim.
9. License variants, now including `backend/pyproject.toml`.
10. **`FAST=1` exits here.**
11. Full exercise: `git init` + commit, `uv sync` / lint / pytest, `pnpm install` /
    **`pnpm audit --prod`** / `lint:check` / `test` / `build`, `make openapi-check`,
    **`make test` including the dual-run contract suite**, `dist/` assertions, the msw-leak
    bundle grep, and **`dev.sh` booted under a pty and stopped with `0x03`**, both ports free
    afterwards.

The audit is here because no lockfile ships: what a generated project resolves is decided by
the registry on the day someone runs `copier copy`, so this template can rot without a single
file in it changing. The two rot classes are handled differently. A FastAPI or pydantic
release rewriting `openapi.json` is *prevented*, by the upper bounds in
`pyproject.toml.jinja` — that failure is not worth detecting, it is worth not having. A new
advisory, or a floor that stops resolving, is *detected*, by this run.

**A scheduled trigger was considered and rejected.** It would close the remaining gap —
neither mechanism fires while nobody touches the repo — but a red main with no diff to
bisect and no PR to attach to is a poor signal, and running the three-variant matrix weekly
is a lot of compute for it. The gap is real and accepted: rot is found on the next `make
check`, which the pre-commit hook makes hard to skip.

### 14. CONTEXT.md glossary

Inherits the TS sibling's six terms (generator, template, generated project, agent-ready
layer, vendored code, mock mode) and adds three:

- **half** — one of the two independently-operable stacks of the generated project,
  `frontend/` or `backend/`. Each can be installed, linted, tested, and built without the
  other's toolchain present. *Avoid: app, side, package, workspace, service.*
- **live mode** — the frontend running with MSW off, requests answered by the backend half.
  *Avoid: real mode, connected mode, integrated mode, backend mode.*
- **contract artifact** — a committed file derived from backend code that encodes the
  contract: `openapi.json` and `frontend/src/api/schema.ts`. Never hand-edited; changed only
  by `make openapi`. The opposite of vendored code, which a tool wrote but you own and may
  edit. *Avoid: generated types, the schema, spec files.*

Prose rule: the generated project is "the app"; `app/` is only ever a directory name.

### 15. ADRs — exactly three

- **0001 — Copier over a bespoke CLI.** Surprising beside the TS sibling; hard to reverse once
  projects carry `.copier-answers.yml`.
- **0002 — Code-first contract with committed artifacts.** Every reviewer asks why generated
  files are in git; the answer (each half independently operable) is the whole design.
- **0003 — The backend is an application, not a library.** No build backend, no dynamic
  versioning, no `py.typed`, flat `app/`, no version matrix — deliberately unlike the Python
  sibling.

A fourth is worth writing if the `pnpm dlx openapi-typescript` isolation (Finding 1) survives
into the build: it is surprising, load-bearing, and a real trade-off.

### 16. Pitfalls for `docs/development.md`

Contract drift (the error message names `make openapi`); regenerate-never-hand-merge on
artifact conflicts; MSW silently shadowing a running backend; the `/api` prefix rewrite and
the 307 trap; port collisions; no CORS by design; `pnpm preview` has no proxy; two cool-off
systems with four config points and an asymmetric trust story; seed drift between the stores
accepted by design; POSIX-only `dev.sh` and hooks; `pythonpath` shims because `package = false`
installs nothing; the Input/Output schema-splitting trap if `separate_input_output_schemas` is
ever flipped.

---

## Build order

1. **Repo skeleton + `copier.yml`** — questions, validators, `_message_after_copy` (carry the
   sequencing lesson: `git init` and commit before `make install`). Own `.claude/`/`.entire/`
   without the guard.
2. **Backend half** — graduate the spike's `app/`, `tests/`, `devtools/export_openapi.py`;
   add `pyproject.toml.jinja` with the sibling's ruff/basedpyright/codespell/complexity
   config, `.python-version`, `lint.py`, `complexity.py`.
3. **Contract toolchain** — `make openapi` / `openapi-check`, committed `openapi.json`.
4. **Frontend half** — the TS sibling's template **at `a82ef37` or later** with the spike's
   deltas: generated `schema.ts`, `types.ts` aliases, openapi-msw handlers, proxy config,
   `.env.live`, `contract.test.ts`, the complexity ratchet *and* `conformance.mjs`, and
   `schema.ts` in all three exclusion lists (§9).
5. **Root layer** — `AGENTS.md`, `Makefile`, `devtools/{dev.sh,install-hooks.sh}`,
   `.githooks/pre-commit`, `docs/`, CI.
6. **`check_template.sh`** — §13, then `make check-all` green on all three variants.
7. **Docs, ADRs, CONTEXT.md**; two sibling `updating.md` PRs; tag `v0.1.0`.

Steps 2–4 are the ones the spike de-risked; step 6 is where the remaining time goes.
