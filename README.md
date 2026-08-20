# agent-ready-fullstack-ts-python

[![python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![node](https://img.shields.io/badge/node-24-blue?logo=node.js&logoColor=white)](https://nodejs.org/)
[![react](https://img.shields.io/badge/react-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![fastapi](https://img.shields.io/badge/fastapi-0.141-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Copier](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/copier-org/copier/master/img/badge/badge-grayscale-border.json)](https://github.com/copier-org/copier)

## What is This?

**agent-ready-fullstack-ts-python** is a [Copier](https://copier.readthedocs.io/)
**template** that generates a **monorepo application** — a React frontend talking to a
FastAPI backend — set up to work well with AI coding agents out of the box.

A generated project starts with a working screen: a small CRUD list, wired end to end
through TanStack Query. Run `make dev` and both halves come up, the browser talks to Python,
and the data is real. Run `make dev-frontend` and the same screen works with **no backend
and no Python installed at all**, answered by a [Mock Service Worker](https://mswjs.io/)
layer.

Both of those are permanent modes, not stages. The mock handlers are what every component
test runs against, and they are typed against the backend's own OpenAPI spec, so a handler
that disagrees with the service is a compile error rather than a surprise at runtime.

That is the thing this template exists for. Its two siblings generate artifacts meant to be
*consumed* — [agent-ready-ts](https://github.com/CodeMocsh/agent-ready-ts) generates a React
SPA, [agent-ready-python](https://github.com/CodeMocsh/agent-ready-python) generates a
publishable Python package. This one generates a **system**: two halves that have to agree
with each other. So it ships a contract, and a test suite that runs twice — once against the
mocks and once against the real backend — to prove they do.

## The Contract

The backend is the single authoring point. Everything downstream is derived and committed:

```
backend/app/{models,routes}.py  ->  openapi.json  ->  frontend/src/api/schema.ts
                                                       -> types.ts, mocks/handlers.ts
```

`make openapi` regenerates both artifacts; `make openapi-check` fails if the committed ones
are stale, and so does CI. Committing them is what keeps each half independently operable:
the frontend regenerates its types, runs its tests and builds with no Python present.

## The Stack

| | frontend | backend |
|---|---|---|
| runtime | Node 24 | Python 3.12 |
| package manager | [pnpm](https://pnpm.io/) | [uv](https://docs.astral.sh/uv/) |
| framework | [React](https://react.dev/) 19 + [Vite](https://vite.dev/) 8 | [FastAPI](https://fastapi.tiangolo.com/) + [pydantic](https://docs.pydantic.dev/) v2 |
| routing / state | [TanStack Router](https://tanstack.com/router) + [Query](https://tanstack.com/query) | [uvicorn](https://www.uvicorn.org/), in-memory store |
| styling | [Tailwind CSS](https://tailwindcss.com/) 4 + [shadcn](https://ui.shadcn.com/) on [Base UI](https://base-ui.com/) | — |
| lint + format | [biome](https://biomejs.dev/), React domain on | [ruff](https://docs.astral.sh/ruff/) |
| types | `tsc --noEmit`, TypeScript 7 | [BasedPyright](https://docs.basedpyright.com/) |
| tests | [vitest](https://vitest.dev/) + [Testing Library](https://testing-library.com/), [Playwright](https://playwright.dev/) | [pytest](https://docs.pytest.org/) |
| contract | [openapi-typescript](https://openapi-ts.dev/) + [openapi-msw](https://github.com/christoph-fricke/openapi-msw) | `app.openapi()`, exported in-process |

On top of that stack it adds the **agent-ready layer**:

- **`AGENTS.md` + `CLAUDE.md`** — a single source of agent instructions (`CLAUDE.md` just
  imports `AGENTS.md`, so guidance reaches every agent). It carries an explicit *Approach*
  and a *zero comments* rule that pushes rationale into commit messages and ADRs, a
  carve-out for vendored code so agents don't reformat shadcn components, and a rule for
  contract artifacts, which are regenerated rather than edited. One layer at the root covers
  both halves.
- **A see-what-you-built loop** — Playwright ships wired up in both mock and live mode, and
  `AGENTS.md` tells the agent to run the app and read a screenshot rather than infer
  behaviour from JSX.
- **A contract suite that runs twice** — `frontend/tests/contract.test.ts` exercises the
  real API client against the mock handlers under `pnpm test`, and against the running
  backend through the dev proxy under `make test-contract`. Identical assertions, two
  implementations: the standard way to keep a test double honest. Both runs are enforced in
  the generated project's CI, the second by the one job that installs both toolchains.
- **[Entire](https://entire.io) session-tracking hooks** in `.claude/settings.json` that
  checkpoint agent coding sessions alongside git history. The hooks no-op until the `entire`
  CLI is installed, so they cost nothing until you opt in — and you can delete `.claude/`
  and `.entire/` to drop the feature entirely.
- **A dependency-free agent guardrail** (`.claude/hooks/agent_guard.py`) — a small
  `PreToolUse` hook that blocks a few unambiguously dangerous actions (`rm -rf /`, disk
  wipes, force-push to `main`/`master`, writing `.env` files or hard-coded secrets).
  Standard-library Python, so it works before either half has been installed. Fails open. A
  seatbelt, not a security scanner.
- **Complexity gates on both halves, in their own units** — a per-function limit, a volume
  limit, and a drift ratchet against a committed baseline, each measured on its own side.
  Plus design-system conformance checks on the frontend, which fail closed with a reviewable
  allow-list. The numbers and where they came from are in `docs/agent-tooling.md`.
- **Supply-chain defaults that hold without anyone remembering them** — a 14-day release
  cool-off on both sides (`minimumReleaseAge` for pnpm, `exclude-newer` for uv), pnpm's
  `trustPolicy: no-downgrade`, and SHA-pinned GitHub Actions. These are strict enough to
  have real consequences: dependency floors have to be chosen from outside the cool-off
  window, and when a security patch is itself inside that window it takes an exception
  pinned to the exact patched version. Backend runtime dependencies are bounded above as
  well as below, because no lockfile ships and the OpenAPI artifact is committed.

## Usage

Generate a new project with Copier (installed on demand via
[uv](https://docs.astral.sh/uv/)):

```bash
uvx --exclude-newer "14 days" copier@9.17.1 copy \
  gh:CodeMocsh/agent-ready-fullstack-ts-python my-app
```

You'll be prompted for the project name and a few details (press enter to accept defaults or
fill in `changeme` later). Then follow the printed next steps:

```bash
cd my-app
git init && git add . && git commit -m "Initial commit from agent-ready-fullstack-ts-python"
make install        # both halves, and the git hooks

make dev            # backend on :8000, frontend on :5173
make dev-frontend   # or just the frontend, mock mode, no backend at all
make lint test      # both halves, then the contract suite across both
```

Commit before `make install`: the installer activates the project's git hooks, and that
needs a repository to install them into.

To activate agent session tracking in the generated project (optional):

```bash
curl -fsSL https://entire.io/install.sh | bash   # installs the `entire` CLI
entire enable --agent claude-code                # wires up the repo
```

## Updating an Existing Project

Projects generated from this template record their answers in `.copier-answers.yml`, so you
can pull future template improvements with:

```bash
uvx --exclude-newer "14 days" copier@9.17.1 update
```

Review the diff, resolve any `.rej` files, and run `make openapi` if the contract artifacts
were among them — see [updating.md](updating.md).

## Repo Layout

- **`copier.yml`** — the template's six questions, the one computed answer, and the
  post-copy message.
- **`template/`** — everything rendered into a new project. File and directory names
  containing `{{ ... }}` or `{% ... %}` are Jinja-templated by Copier; files ending in
  `.jinja` have their contents rendered, with the suffix stripped. `template/frontend/` and
  `template/backend/` are the two halves; the root layer above them holds the `Makefile`,
  the contract, the docs, and the agent-ready layer.
- **`devtools/check_template.sh`** — renders the template and exercises the result, both
  halves and the contract between them. CI and the pre-commit hook both call it, so a check
  cannot exist in one and be missing from the other.
- **`docs/adr/`** — the three decisions worth writing down: Copier over a bespoke CLI, a
  code-first contract with committed artifacts, and a backend that is an application rather
  than a library.

See [CONTEXT.md](CONTEXT.md) for what "template", "half", "live mode" and "contract
artifact" each mean here, [AGENTS.md](AGENTS.md) for the constraints that bite when editing
the template, and [updating.md](updating.md) for how this repo stays in step with its two
siblings.

## Working on the Template

```bash
make check        # default variant, end to end
make check-all    # every license variant, as CI does
make fast         # render and assert only, skipping install/lint/test/build
make hooks        # enable the pre-commit hook (once per clone)
```

A full run installs both toolchains, audits the frontend's production dependencies, lints
and tests both halves, regenerates the contract and diffs it, runs the contract suite with
the backend actually serving the frontend, and finally boots `make dev` and stops it to
prove both ports are released. Editing `template/` and trusting the diff proves nothing:
render it and exercise the output.

* * *

*Third sibling of [agent-ready-ts](https://github.com/CodeMocsh/agent-ready-ts) and
[agent-ready-python](https://github.com/CodeMocsh/agent-ready-python), and through the
latter a descendant of [simple-modern-uv](https://github.com/jlevy/simple-modern-uv) by
[jlevy](https://github.com/jlevy).*
