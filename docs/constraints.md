# Constraints

Things a reasonable edit undoes, that fail somewhere far from the edit. Each is here because it
has nowhere better to be: its natural home is a `.json` file or a script, which cannot carry a
comment, or it is a rejected option, or it is a rule about editing the template rather than
running it.

Anything with a home keeps it. `render.sh` explains the unrendered-token assertion in its
failure message. `pnpm-workspace.yaml` explains every supply-chain setting it carries.
`check_template.sh` puts the reason for an assertion in the message the assertion fails with,
where the assertion does not make the reason plain. `backend/tests/tier.py` explains why the gate
fetches no browser.

## Where Jinja may be used

Copier has two mechanisms. A **`.jinja` suffix** renders a file's contents and is stripped on
output; a file without it is copied byte for byte. **Jinja in a filename** makes the file
conditional or renames it, so `{% if package_license != 'None' %}LICENSE{% endif %}.jinja`
emits nothing at all when it evaluates empty.

**Never suffix a `.ts`, `.tsx` or `.py` source file.** The suffix takes the file out of its own
toolchain: the editor stops type-checking it, biome and ruff stop seeing it, and every gate this
template ships stops applying to it. Anything project-specific a source file needs comes from a
value it reads at runtime, not from a token.

**A workflow file is never `.jinja`.** GitHub Actions uses `${{ }}` and so does Jinja. Keeping
`.github/workflows/*.yml` unrendered means the two syntaxes never meet. This binds both trees:
the workflow the template ships, and the ones this repo runs on itself.

**The backend half is token-free.** Its only `.jinja` is `pyproject.toml.jinja`. Everything in
`backend/app/` feeds `openapi.json`, a committed artifact sworn never to be hand-edited, so a
project name in the FastAPI title would make every generated project's spec differ from this
one — surfacing as a failing `make openapi-check` in someone else's project. The demo API is
titled `"Tasks API"` everywhere; rebranding happens when a user replaces the demo and
regenerates in the same commit.

**A new question needs a behavior-preserving default**, because `copier update --defaults` has
to be a no-op for an existing project.

## The gate downloads actionlint, so it needs a network

`check_template.sh` fetches a pinned actionlint and verifies its hash before linting the
workflows in both trees. A workflow cannot report its own breakage, so the gate is the only
place that catches a malformed one before it is pushed.

The cost is that the pre-commit hook now fails with no network, where before it only needed one
for the render and the installs. There is no cache: a hit that could be stale is worse here
than a download, because the thing being verified is a binary. Bump the version and all four
platform hashes together -- they are the same release, and a hash that belongs to another one
fails closed.

## Stopping a half means stopping a process group, and `/bin/sh` fights you twice

`uv run` and `pnpm` both exec a launcher that spawns the real server as a child. Killing the
pid the script holds reaches the launcher and leaves the server. So `dev.sh` and
`contract-test.sh` stop a process group instead. Two things break that, both silently, and
both only where `/bin/sh` is dash — which is Debian, Ubuntu, and therefore CI.

`set -m` does not make the group. dash turns job control off when it has no controlling
terminal, writes one line to stderr, and carries on. Both scripts call `own_group` instead — a
four-line shell function that execs `python3`, calls `os.setsid()`, and execs the server. It
needs no terminal, and the group leader is the pid the script already holds. **That makes
`python3` on `PATH` a requirement of `make dev` and `make test-contract`**, which is why
`docs/installation.md` names it; `uv`'s own Python does not satisfy it.

`dev.sh` still sets `set -m`, and not for the backend — `own_group` covers that. It is there so
vite runs as a foreground job of its own. `contract-test.sh` runs neither half in the
foreground and sets nothing.

`kill` does not take `--`. `kill -TERM -- -123` is the POSIX spelling, and dash's builtin
answers `Illegal number: -` and sends no signal at all. Write `kill -TERM -123`. A negative pid
needs no separator anywhere.

Neither failure is visible where it happens: the ports come free, the suite passes, and the
orphan surfaces minutes later as a temp directory that will not delete. So the gate asserts it
rather than trusting it — `need_nothing_outlived` runs after the contract suite and after the
dev loop, and names the process and the step.

That assertion only fires on the platform that leaks, and `/bin/sh` on a mac is bash, which
takes both spellings. A laptop run would stay green while every CI run leaked, so the gate also
greps both scripts for `own_group` and against both broken spellings. **Do not add a single-pid
fallback behind either kill** — that fallback is what hid both of these for as long as they
existed, and the grep refuses it.

The gate drives the dev loop under a pty and writes Ctrl-C (byte `0x03`) to it. `dev.sh` keeps
vite in the foreground, so a signal to the script alone waits for vite to return, and a plain
`kill` deadlocks. The reader drains the pty all the time: a full pty buffer stops `dev.sh`.
The gate starts `app.serve` from the venv's interpreter rather than through `uv run`, so the pid
it holds is the server's.

## `render.sh` copies the tree through a file, not a pipe

`render.sh` writes the working tree to a tar file, then extracts it. Do not change that to
`tar -c | tar -x`. In a pipeline, `set -e` sees only the status of the last command. A failed
archive step then extracts a partial tree, and the gate passes on a template that is not the one
on disk. `set -o pipefail` is not POSIX: dash, which is `/bin/sh` on CI, refuses that line.

## A script that git runs as a hook inherits `GIT_DIR`

Git exports `GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE` and related variables to each hook it
runs. `render.sh` and `check_template.sh` run git in temp directories, so they unset these first.
Remove the `unset` and every git command in the render points at the calling repository. This
fails only when the script runs from the pre-commit hook, never when you run `make check` by hand.

## The shared-prose check needs `sed -E` and an end heading from each file

`check_template.sh` compares the principle sections of the two `AGENTS.md` files over a `sed`
range. BSD sed has no `\|` alternation in a basic regex, so the range needs `sed -E`. The end
pattern names the first heading that each file owns. An end pattern that only one file carries
runs the range to the end of the other file, and every local section then reports as drift.

## The gate picks both ports first, in one process

`check_template.sh` sets `BACKEND_PORT` and `FRONTEND_PORT` before its first step. Set later,
the steps above them run on `:8000` and `:5173`, which a `make dev` in another checkout often
holds. One process allocates both and keeps each socket bound until it has both, so the two
cannot come back as the same number. The probe tries both loopback families: `localhost` is two
addresses, and a vite that holds `[::1]` only passes an IPv4 probe until `--strictPort` refuses
in the middle of the run.

The live contract suite runs under `env -u BACKEND_PORT -u FRONTEND_PORT`. That makes
`contract-test.sh` run its own allocator, which is what a user gets. Without the `env -u`, the
suite inherits the gate's pair and the allocator runs nowhere.

The one-origin step starts `app.serve` under `env -u DATABASE_URL`. Without it, the step runs on
the database the shell exports, and the suite's cleanup removes every task in that database.

## The gate's exit handler reads its status first and hands it back last

`set -e` is live inside a trap. A `kill` of a process that has gone returns non-zero and skips
the `rm -rf` after it, so every command in `cleanup` that can fail carries `|| true`. The handler
reads `$?` on entry and ends in `exit "$status"`. Without that, its last command decides the exit
status of the whole script: a run that printed `==> OK` once failed on an `rm`. `INT` and `TERM`
pass `130` and `143`, because `$?` on the way into an interrupt is the last command that
succeeded, which reports Ctrl-C as a pass.

The handler waits on `serve_pid` before it scans for leaks. A scan before the reap names the
server that the handler itself is stopping. `processes_under_work` captures `ps` before it runs
`grep`. In a pipe, `ps` lists the `grep` beside it, whose arguments hold `$WORK`, so every call
reports a leak.

## Two steps in the gate look wrong on purpose

`pnpm audit` exits non-zero on a finding, so the gate ignores its status (`|| true`) and reads
the JSON report on stdout. The Python that follows fails on each high or critical advisory. When
no report arrives, the step says so and passes: an unreachable registry is not a vulnerability,
and a red run that means "no network" teaches people to ignore red.

The conformance fixtures run outside the rendered frontend, where no `package.json` is in reach.
That is deliberate. `gate.mjs` `section()` returns `{}` when there is no `package.json`, so
`conformance.mjs` uses its own defaults and the project's `allow` list stays out of a run that
proves each rule fires. One default names a theme file that exists only in a project, so the
fixtures pass `--theme-file` an empty file.

The comments inside those fixtures are data, not a breach of *Zero comments*. The scanner must
see through a comment in both directions: `commented.tsx` puts an unbalanced `)` before a
`fetch`, and `good.tsx` puts the word `fetch(` in an effect that only subscribes. Strip them,
and the gate stays green while it tests neither case.

## The lint fixing variants run last in the gate

The final section of `check_template.sh` drives `pnpm lint` and `backend/devtools/lint.py` without
`--check`. Those run `biome check --write`, `ruff --fix`, `ruff format` and
`codespell --write-changes` over the rendered project. Earlier in the script they would repair
what an earlier step exists to catch, move backend source under `make openapi-check`, and pass a
template that ships unformatted source. Nothing may follow that section.

## `openapi-typescript` runs through `pnpm dlx` at an exact pin

It must not become a devDependency. It declares `peerDependencies: { typescript: "^5.x" }` and
builds its AST with `ts.factory`, which TypeScript 7 — the native port — does not have. The
frontend is on TypeScript 7, so installing the generator into that half crashes at run time, in
`ts.mjs`, on a `createKeywordTypeNode` that no longer exists.

pnpm only *warns* about the peer mismatch, so this fails when someone runs `make openapi`, not
when they install. `pnpm dlx` gives it its own TypeScript in its own resolution.

The trade is that the version lives in a script string rather than the lockfile: **pin it
exactly and bump it deliberately**, because a floating version would silently rewrite a
committed contract artifact. When openapi-typescript supports TypeScript 7, collapse it back
into a devDependency.

## `tsconfig.json` expresses the `@/` alias with `paths` alone

TypeScript 7 removed `baseUrl`. Reintroducing it because a tutorial uses it breaks the build in
a way the error message does not explain.

## `src/api/schema.ts` must stay in every exclusion list

The generated contract artifact is a plain `.ts` file that grows with the API and is invisible
to every automatic skip. It has to be named in `files.includes` in `biome.json`, and in
`complexity.exclude`, `conformance.exclude` and `comments.exclude` in `package.json` — JSON
files, none of which can say why.

Naming it in all but one passes today and fails later for reasons that will not be obvious:
`openapi-typescript` writes the spec's descriptions out as JSDoc, so the comment gate fails
first. `check_template.sh` asserts every list, and the same applies to any generated or vendored
file added under `frontend/src/` afterwards.

## Dependency floors must clear the cool-off

A `>=` floor whose value is the *latest* release **cannot resolve**: the cool-off forbids the
only version that satisfies it. When bumping in either half, pick the highest version published
outside the window rather than the newest. A security fix that cannot wait gets an exclusion.
`pnpm-workspace.yaml` and `pyproject.toml.jinja` each say how. `check_template.sh` holds a pnpm
exclusion to one exact version. A generated project's `backend/tests/test_supply_chain.py` holds
a uv exclusion to one timestamp inside the cool-off, with a floor.

## `apps/web` + `apps/api` was rejected, and stays rejected

That layout advertises a many-package workspace with shared `packages/*`, which this is not —
and pnpm's workspace machinery cannot span into Python anyway. `frontend/` and `backend/` are
toolchain boundaries, not workspace members. There is no root `package.json`, and
`pnpm-workspace.yaml` stays inside `frontend/` as a settings carrier.

## The backend half is an application, and the library shape was rejected

No `[build-system]`, no dynamic version, no `py.typed` and no `src/` layout, so a
`pyproject.toml` ported from a library template does not apply here — and the two look alike
enough to tempt the copy. `package = false` installs nothing, which is why pytest sets
`pythonpath` and `devtools/export_openapi.py` adjusts `sys.path`. Both read as workarounds and
both are load-bearing.

**`backend/.python-version` is load-bearing too, and deleting it does not fail loudly.** A
`requires-python` floor with no pin does not mean "supports many versions". uv resolves the
environment to whatever the machine happens to offer, which on one machine was 3.14.7. The pin
is the number uv, the gate and every laptop agree on.

**The package is named `app` in every generated project, and the name is fixed.** That is what
makes `uvicorn app.main:app` — the invocation in every piece of FastAPI documentation — work
unedited, and it is one question the generator does not have to ask. Renaming it moves the
pytest setting, the exporter's `sys.path` insert, and every import in `app/` and `tests/`
together, so it is the wrong template for anyone running several services in one repository.
