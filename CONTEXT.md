# agent-ready-fullstack-ts-python

The shared language of this repo. It exists because "agent-ready-fullstack-ts-python" names
three different things depending on who is speaking — the thing you run, the tree it copies,
and the project you end up with — and because the project it produces has two of everything,
which doubles the number of words that can be used loosely.

This file is a glossary. It carries no implementation detail and no decisions; those live in
commit messages and `docs/adr/`.

## Language

**Generator**:
[Copier](https://copier.readthedocs.io/) driven by this repo's `copier.yml`. This repo ships
no generator code of its own; the questions, their validators, and `_subdirectory: template`
are the whole of it.
_Avoid_: scaffolder, tool, script

**Template**:
The inert tree in `template/`. It is not a working project and cannot be built, linted, or
tested in place; it only becomes any of those once rendered.
_Avoid_: boilerplate, skeleton, starter

**Generated project**:
The output of running the generator — a real project on a user's disk, no longer coupled
to this repo. It records the answers it was rendered from in `.copier-answers.yml`, which
is what `copier update` reads. It is fine to call one "the app"; `app/` is only ever a
directory name.
_Avoid_: rendered template, output, instance

**Half**:
One of the two independently-operable stacks of the generated project, `frontend/` or
`backend/`. Each can be installed, linted, tested, and built without the other's toolchain
present.
_Avoid_: app, side, package, workspace, service

**Contract artifact**:
A committed file derived from backend code that encodes the contract: `openapi.json` and
`frontend/src/api/schema.ts`. Never hand-edited; changed only by `make openapi`. The
opposite of vendored code, which a tool wrote but you own and may edit.
_Avoid_: generated types, the schema, spec files

**Agent-ready layer**:
The portion of the template that is deliberately stack-independent: the `AGENTS.md`
*Approach* and *Zero comments* sections, the Entire session-tracking hooks, the agent
guard, and the one-check-script contract shared by CI and the pre-commit hook. It is the
only thing kept in step with the two siblings.
_Avoid_: agent tooling, agent setup, the Claude layer

**Vendored code**:
Third-party source that lands inside a generated project's own tree rather than in
`node_modules` — shadcn components and the Mock Service Worker script. It is committed but
not authored here, so the rules that govern authored source do not reach it.
_Avoid_: generated code, copied code, third-party code

**Mock mode**:
A generated project's frontend running with its Mock Service Worker handlers intercepting
network calls, so the frontend half is fully usable with no backend behind it. A build-time
flag, not a code path the application is aware of.
_Avoid_: mocking, offline mode, dev mode

**Live mode**:
The frontend running with MSW off, requests answered by the backend half.
_Avoid_: real mode, connected mode, integrated mode, backend mode
