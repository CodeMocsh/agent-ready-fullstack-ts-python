# 0003. The backend is an application, not a library

## Status

Accepted, 2026-08-16.

## Context

The backend half was shaped by agent-ready-python's conventions, and inheriting that
sibling's `pyproject.toml` wholesale would have been the path of least resistance. That
template generates a **publishable package**: a hatchling build backend, a version derived
dynamically from git tags, a `py.typed` marker so downstream type checkers trust the
annotations, a src-layout (`src/{{ package_module }}/`), a CI matrix across Python 3.11 to
3.14, and a workflow that publishes to PyPI on a tag.

Every one of those exists to serve a consumer this backend does not have. Nothing imports
it. Nothing installs it from an index. It is deployed, behind a process manager, on a
runtime it chooses for itself. A build backend with no wheel to build, a version nobody
reads, and a compatibility matrix across four interpreters the deployment will never use are
all ceremony around an artifact that is never produced.

There is a second reason to be deliberate here. A spike found that with `requires-python =
">=3.12"` and no `.python-version`, uv resolved the environment to whatever the system
happened to offer — Python 3.14.7 on the machine in question. A range plus no pin is not
"supports many versions"; it is "runs on whichever one you happened to have", which is the
failure mode a matrix is supposed to prevent and does not.

## Decision

The backend half is configured as an application:

- **No `[build-system]` table and no `[project.scripts]`.** Nothing is built into a wheel.
- **No dynamic versioning.** `version = "0.0.0"`, static, and the deployed artifact is a
  commit.
- **No `py.typed`.** There is no downstream type checker to signal to.
- **Flat `app/`, not src-layout, and `package = false`.** The package is named `app` in
  every generated project, so `uvicorn app.main:app` — the invocation in every FastAPI
  reference — works verbatim, and the generator has one fewer question to ask.
- **One Python version.** `requires-python = ">=3.12"` with `.python-version` pinning the
  number that uv, CI and every laptop actually resolve to.
- **No CI version matrix and no publish workflow.** CI runs the runtime the application
  deploys on.

The frontend half already takes the same position for the same reason: `"private": true`, a
fixed `"version": "0.0.0"`, Node 24 and nothing else. Both siblings matrix because they
generate broadly-consumed artifacts; an application tests what it runs.

## Consequences

**Positive.** The configuration is smaller and every line in it does something. There is no
build backend to keep current, no versioning plugin, no release workflow to audit for token
scope, and no tag ceremony before a deploy.

`uvicorn app.main:app` works the moment the project is generated, and matches every piece of
FastAPI documentation a person or an agent will find. `app` being a fixed name also removed
`package_module` from the generator's questions, which is one less answer that has to be
consistent across four naming conventions.

CI is fast and honest. Two jobs, one runtime each, and a green run means the thing that
deploys works — not that four interpreters agreed about a package nobody installs.

`.python-version` is what makes the environment reproducible. It is load-bearing rather than
decorative, and removing it does not fail loudly; it just quietly resolves to something
else.

**Negative.** This diverges from agent-ready-python in the file most likely to be diffed
against it. A change ported from that sibling's `pyproject.toml` may assume a build backend,
a dynamic version, a `src/` layout or a matrix, and cannot be applied blind. `updating.md`
records the stacks as per-repo forever, but the two `pyproject.toml` files will keep looking
similar enough to tempt a copy.

`app` is a fixed name, and fixed names cost something. Two backends in one workspace
collide. Renaming it is not a rename of one directory: `uvicorn app.main:app`, the
`pythonpath` setting in pytest, the `sys.path` insert in `devtools/export_openapi.py`, and
every import in `app/` and `tests/` move together. That is a deliberate trade for the
reference invocation working out of the box, and it is the wrong trade for anyone who
intends to run several services in one repository.

`package = false` installs nothing into the environment, so `app` is importable only from
the project root. That is why pytest sets `pythonpath = ["."]` and why the exporter adjusts
`sys.path`. Both read as workarounds to anyone who has not read this decision, and both are
load-bearing.

A single Python version means an incompatibility with another one is discovered at
deployment rather than in CI. That is acceptable precisely because the deployment runtime is
pinned in the same repository; it stops being acceptable the moment this backend is expected
to run somewhere that chooses its own interpreter, at which point a matrix earns its keep
and this decision should be revisited rather than worked around.
