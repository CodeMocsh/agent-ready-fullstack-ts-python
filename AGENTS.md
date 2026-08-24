# Project Instructions for AI Agents

Instructions for AI coding agents working on **agent-ready-fullstack-ts-python itself** —
the Copier template. Follows the [AGENTS.md](https://agents.md) convention.

> **Important:** this repo is a *template*, not a runnable project. Nothing at the root
> installs, builds, or serves anything. Everything an end user gets lives under `template/`,
> is inert until [Copier](https://copier.readthedocs.io/) renders it, and cannot be linted or
> tested in place. Editing `template/` and running nothing proves nothing: **render it and
> exercise the output.**
>
> Two stacks live under `template/` — a pnpm React frontend and a uv FastAPI backend — and
> neither of them runs during generation. Do not reason about a template file as though it
> were executing.

[CONTEXT.md](CONTEXT.md) defines the vocabulary — generator, template, generated project,
half, live mode, contract artifact, agent-ready layer, vendored code, mock mode. Use those
words and no synonyms.

## Repo Layout

- **`copier.yml`** — the six questions, their validators, the one computed answer, and the
  `_message_after_copy` shown after generation. `_subdirectory: template` means only
  `template/` is rendered.
- **`template/`** — the files rendered into a new project, in two halves plus a root layer.
- **`template/.claude/` + `template/.entire/`** — the agent-ready layer that ships in
  generated projects.
- **`devtools/check_template.sh`** — renders the template and exercises the result. The
  pre-commit hook and `make check` both call this one script, and it is the whole of the
  enforcement: no workflow runs, so nothing checks a push you did not check yourself.
- **`docs/adr/`** — the four decisions that are surprising enough to need writing down.

There is no `src/`, no `package.json`, and no `licenses/` directory. Copier removes the need
for generator code, and its Jinja handles the license variants directly.

## Copier conventions

Two mechanisms, and both are easy to get subtly wrong:

- **A `.jinja` suffix renders a file's contents**, and the suffix is stripped on output. A
  file without it is copied byte for byte.
- **Jinja in a *filename*** makes the file conditional or renames it. The name
  `{% if package_license != 'None' %}LICENSE{% endif %}.jinja` emits nothing at all when it
  evaluates empty.

Four rules govern where those may be used, and each exists because breaking it fails
somewhere far from the edit:

**Never suffix a `.ts`, `.tsx`, or `.py` source file.** A `.jinja` suffix takes the file out
of its own toolchain: the editor stops type-checking it, biome and ruff stop seeing it, and
every gate this template ships stops applying to it. Anything project-specific a source file
needs comes from a value it reads at runtime, not from a token.

**A workflow file, if one ever returns, is never `.jinja`.** GitHub Actions uses `${{ }}`
and so does Jinja. Keeping `.github/workflows/*.yml` unrendered means the two syntaxes never
meet, and no expression ever has to be escaped. None ships today —
[docs/adr/0004](docs/adr/0004-no-workflow-runs-the-gate.md) says why.

**The backend half is entirely token-free.** Its only `.jinja` is `pyproject.toml.jinja`.
Nothing in `backend/app/` may carry a token, because everything in `app/` feeds
`openapi.json` — a committed contract artifact that is sworn never to be hand-edited. A
project name in the FastAPI title would make every generated project's spec differ from the
one in this repo, and the drift would surface as a failing `make openapi-check` in someone
else's project. The demo API is titled `"Tasks API"` everywhere; rebranding happens when a
user replaces the demo and regenerates the artifacts in the same commit.

**Defaults must stay behavior-preserving.** `copier update --defaults` has to be a no-op for
an existing project, so a new question needs a default that reproduces today's output.

The safety net for all of this is in `check_template.sh`: after rendering it asserts that no
`{{ … }}` survives, no `{% … %}` survives, and no `*.jinja` file remains on disk. The
forgotten-suffix failure mode — a token left in a file that was never rendered — produces a
generated project containing literal `{{ package_name }}`, and that assertion is what
catches it.

## Constraints that bite

Four things in this template are load-bearing in ways a reasonable edit would undo.

**`openapi-typescript` runs through `pnpm dlx` at an exact pin, and must not become a
devDependency.** It declares `peerDependencies: { typescript: "^5.x" }` and builds its AST
with `ts.factory`, which TypeScript 7 — the native port — does not have. The frontend is on
TypeScript 7 to stay in step with the TS sibling, so installing the generator into that half
crashes at run time, in `ts.mjs`, on a `createKeywordTypeNode` that no longer exists. pnpm
only *warns* about the peer mismatch, so this fails when someone runs `make openapi`, not
when they install. `pnpm dlx openapi-typescript@7.13.0`
gives it its own TypeScript in its own resolution, at a cost of about a second warm. The
trade, recorded in `template/docs/development.md`, is that the version lives in a script
string rather than the lockfile: **pin it exactly and bump it deliberately**, because a
floating version would silently rewrite a committed contract artifact. When
openapi-typescript supports TypeScript 7, collapse it back into a devDependency.

**Dependency floors must clear the 14-day cool-off on both sides.** A `^` range or a `>=`
floor whose value is the latest release cannot resolve, because the policy the template
enforces on itself forbids the only version that satisfies it. This applies to
`template/frontend/package.json` under `minimumReleaseAge: 20160` and to
`template/backend/pyproject.toml.jinja` under `exclude-newer = "14 days"`. When bumping
either, pick the highest version published *more than 14 days ago*, not the newest.

**The `trustPolicyExclude` entry in `template/frontend/pnpm-workspace.yaml` is
load-bearing.** `shadcn` reaches `@babel/core` → `semver@6.3.1`, which the
`trustPolicy: no-downgrade` setting reads as a takeover. Removing the exclusion breaks both
`pnpm install` and
`pnpm dlx shadcn add` in every generated project — `dlx` reads the project's settings too.
Keep it pinned to the exact version.

**`src/api/schema.ts` must stay in four exclusion lists.** The generated contract artifact
is a plain `.ts` file that grows with the API and clears the 500-line file gate early, and
it is invisible to every automatic skip. It has to be named in `files.includes` in
`template/frontend/biome.json`, and in `complexity.exclude`, `conformance.exclude` and
`comments.exclude` in `template/frontend/package.json`. Three out of four passes today and
fails later for reasons that will not be obvious — `openapi-typescript` writes the spec's
descriptions out as JSDoc, so the comment gate is the one that fails first.
`check_template.sh` asserts all four.

`devtools/check_template.sh` catches every one of these, but only in a full run.
`make fast` will not.

## Making Changes

Edit files under `template/` to change what generated projects receive. After any change,
**render the template and exercise the output** rather than trusting the diff:

```bash
make check        # default variant, end to end
make check-all    # every license variant
make fast         # render and assert only, skipping install/lint/test/build
```

All three call `devtools/check_template.sh`, which is also what the pre-commit hook runs.
That was always the point — a check cannot exist in CI and be missing locally — and since
the workflow was removed it is the only thing standing between a change and `main`, so
**`make hooks` is not optional**. It installs a shim per committed hook rather than setting
`core.hooksPath`; the reasoning is in `devtools/install-hooks.sh`. An unarmed clone commits
unchecked and nothing anywhere notices, which is the cost recorded in
[docs/adr/0004](docs/adr/0004-no-workflow-runs-the-gate.md).

The script renders from the working tree rather than from a tag, so it validates what you
are about to commit.

A full run installs both toolchains, lints and tests both halves, regenerates the contract
artifacts and diffs them, and runs the contract suite with the backend actually serving the
frontend — which is the only step that proves the two halves interoperate. Levels below it
all pass green on a project whose frontend cannot reach its backend at all. It also audits
the frontend's production dependencies and boots `make dev` to check both ports are freed.

**No test the template ships may skip itself.** A test that needs a daemon or a browser goes
in a tier — a folder declared in `template/backend/tests/tiers.py` and run by a target of its
own. A skip does not: it exits 0 and looks like a test that passed, and `check_template.sh`
and the generated project's `test_gate.py` both fail on one. Every other test file mirrors
the source it covers. Tiers are named for what they need, never for who runs them —
`check_template.sh` runs `make db-test` itself wherever Docker answers. Layout in
`template/AGENTS.md.jinja`.

No step downloads a browser. The generated project ships Playwright specs, in mock mode and
in live mode, and the gate runs neither — a deliberate trade recorded in
`template/docs/development.md`. If you change UI in `template/frontend/`, run
`pnpm test:e2e` inside a rendered project yourself.

## Distribution

Nothing is published. The template is run straight from the repository:

```bash
uvx --exclude-newer "14 days" copier@9.17.1 copy \
  gh:CodeMocsh/agent-ready-fullstack-ts-python my-app
```

That works against a private repository because git supplies the credentials. Releases are
tagged `v0.x.y`, because `copier update` resolves against tags — an untagged change is
invisible to every generated project.

## Upstream

This repo is the third sibling of
[agent-ready-ts](https://github.com/CodeMocsh/agent-ready-ts) and
[agent-ready-python](https://github.com/CodeMocsh/agent-ready-python), and it is a **pure
downstream consumer** of the agent-ready layer. Shared-layer files here are read-only: a fix
discovered in this repo is PR'd to the repo that owns the file and pulled back, never
originated here. [updating.md](updating.md) says which repo owns what, and how to check for
drift.
