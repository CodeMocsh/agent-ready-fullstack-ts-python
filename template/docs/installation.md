# Installation

Two toolchains, one per half.

## Prerequisites

**Node 24+ and pnpm**, for the frontend half:

```bash
brew install node                                     # macOS
corepack enable && corepack prepare pnpm@latest --activate
```

**uv**, for the backend half. It installs the Python the application runs on, so you do not
need a system Python of any particular version:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`make`, `git`, `curl`, `perl` and `python3` complete the set; all five ship with the Xcode command line tools
on macOS and with build-essential and the base system on Debian and Ubuntu. `python3` is not
the application's Python — the one uv installs is. `devtools/dev.sh` and
`devtools/contract-test.sh` use it to put each half in a process group of its own, which is the
only way they can stop what they started. `devtools/gate.sh` uses `perl` to take the lock that
keeps two gates of one project from running at once, because macOS ships no `flock`.
`make install` uses `curl` to fetch a pinned gitleaks, checked against `devtools/gitleaks.sums`,
for the secret scan in the gate.

## Install

```bash
make install
```

That runs `pnpm install` in `frontend/`, `uv sync --all-groups` in `backend/` (which installs
the Python version pinned in `backend/.python-version`), activates the versioned git hooks, and
fetches what the gate and the browser tiers need.

It also writes `frontend/pnpm-lock.yaml` and `backend/uv.lock`, which do not ship with the
template: a project generated today should resolve against today's registry rather than against
whichever day the template was last touched. **Commit both.** Without them the next clone
resolves its own, and two checkouts of the same commit build different trees — the kind of
difference that surfaces as a bug nobody can reproduce.

To work on one half only:

```bash
pnpm -C frontend install
cd backend && uv sync --all-groups
```

Neither half needs the other's toolchain to install, lint, test or build.

## Verify

```bash
make lint test
```

The last step starts both halves and runs the contract suite against the real backend, so a
green run means the two agree.

## Updating from the template

`.copier-answers.yml` records the template this project came from and the exact commit, which
is what lets a later template change reach a project generated months ago:

```bash
uvx --exclude-newer "7 days" copier@9.17.1 update
make install
make openapi
make lint
make schema
make pre-commit
```

Copier resolves against **tags**, so `update` brings you to the template's newest release
rather than whatever is on its default branch. It is a three-way merge against the commit you
generated from, so commit or stash first.

What to expect, because it is not all free:

- **Files you never touched update cleanly** — tooling, gates, `Makefile` targets, docs, ADRs.
- **Some files are yours after the first copy.** `copier.yml` lists them under
  `_skip_if_exists`, and an update never touches them. Each code module holds only your list; the
  module that reads it is the template's, and keeps updating. `docs/frontend.md`,
  `docs/backend.md` and `docs/development.md` are yours too: rewrite them with this project's
  detail. A later template change to one reaches only new projects.
- **Stop receiving the example resource once you have replaced it.** Record the answer before
  you update: set `example_resource: false` in `.copier-answers.yml`, commit, then run the
  update. From then on an update leaves the example resource's own files alone, and does not
  re-add any you deleted. Everything else keeps updating. **Do not give the answer on the update
  itself**, with `--data example_resource=false` or at the prompt: Copier then deletes the
  example's files, your edits included. The file says never to edit it by hand. This one edit
  is expected, and Copier keeps the value when it writes the file again.
- **Drop the GitHub workflow if your checks run somewhere else.** Answer once, on the update
  itself: `uvx --exclude-newer "7 days" copier@9.17.1 update --data github_ci=false`. On this
  question the update is meant to delete: it removes the workflow and
  `backend/tests/test_workflow.py`, even a workflow you edited, so commit first. A project
  generated before 0.33.0 keeps its `.github/`: delete it yourself. `docs/development.md` is
  yours, so remove what it says about the workflow too. A new project answers the same question on
  its first copy.
- **Stop receiving the identity stub once you authenticate.** Record the answer the same way:
  set `identity_stub: false` in `.copier-answers.yml`, commit, then update. An update then leaves
  `app/identity/__init__.py` and its tests alone, and still brings `app/identity/shared.py`. Given
  on the update itself, the answer deletes them. A project generated before 0.36.0 has its
  resolver in `app/identity.py`. With `identity_stub: false`, move that file to
  `app/identity/__init__.py` and commit before the update: a folder without `__init__.py` loses
  to a module of the same name. After the update, rename `tenant_for` to `principal_for`, return
  a `Principal`, and import `Unauthenticated` and the other shared names from
  `app.identity.shared`. With `identity_stub: true`, the update deletes `app/identity.py` and any
  edit in it.
- **Files you rewrote come back as conflicts.** The modules under `app/models/` and
  `app/routes/tenant/` are the first any real project replaces. You are porting a pattern rather than accepting a patch.
- **Regenerate the contract and the complexity baseline afterwards.** `openapi.json`,
  `frontend/src/api/schema.ts` and `backend/.complexity-baseline.json` are generated from your
  code, so an update never touches them. `make openapi` rewrites the first two and `make lint`
  the third; if `make lint` reports that complexity rose past the tolerance, the template's
  code is why, and the message says how to record it.
- **Regenerate the schema artifacts too.** `deploy/schema.sql` and `backend/.schema-baseline.json`
  are generated from your `ddl.py`, which by then holds your entries as well as the template's.
  A merged copy of either describes neither project, and `make pre-commit` says so. `make schema`
  rewrites both. Commit them with the update.
- **A uv exclusion table you already have can come in twice.** From 0.35.0
  `backend/pyproject.toml` ships an empty `[tool.uv.exclude-newer-package]`. The merge can keep
  both headers with no conflict, and uv then refuses the file. Keep one header and move your
  entries under it. `backend/tests/test_supply_chain.py` then refuses each entry that is not a
  timestamp inside the cool-off with a floor, and its message says what to do. Your own
  `docs/development.md` keeps the cool-off it states, so correct it by hand.
- **A rule the template adds can refuse code you already have.** From 0.39.0 `make lint` refuses a
  comment in a script as well as in source. From 0.41.0 it refuses a blocking call in an
  `async def`, by ruff's `ASYNC2` rules. `cd backend && python3 devtools/comments.py --strip --tree ..`
  removes the comments the check refuses, and no other text. It stops at the first comment it
  cannot remove without changing what the code does, names it, and leaves that file as it was.
  Remove that one by hand and run it again. Read the diff before you commit it:
  a reason that a comment held belongs in the commit message. `uv run ruff check --select ASYNC2`
  names each blocking call. Move it into a `def` or into `asyncio.to_thread`.
- **From 0.42.0 a write with no actor fails.** Postgres records every write to a table in
  `TENANT_TABLES`, and refuses one when nothing names an actor. A request names its principal's
  actor through `audit_events.serve`: if your identity dependency calls `log.name_principal`,
  call `audit_events.serve` instead. A resolver that writes before it has a principal runs that
  write inside `audit_events.acting_as`. Every worker loop, job and script that writes runs
  inside `audit_events.acting_as(actor)`. An app acting for a person names the person in
  `on_behalf_of`. Until each one does, its first write raises `a write to <table> names no
  actor`, and the loop stops. Your own Postgres tests and fixtures that write rows do the same.
  Store methods no longer take an actor, and record no audit event: delete the recording you
  added for 0.41.0, or each write records two events. `audit_event`, `absent`, `written`,
  `AuditEvent` and `app.store.TASKS` are gone. If you merged `app/store/pg.py`,
  `app/store/migrate.py` or `app/store/ddl.py` by hand, keep `setup=_name_who_acts` on
  `create_pool`, `MIGRATION_ACTING_SQL` in `apply`, and the audit entries. A tenant table with no
  primary key cannot be written: give it one. After the first
  `make migrate` on 0.42.0, an instance of an earlier release cannot write, so replace them all
  in the same release.
  [adr/template/0017](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md)
  says why.
- **Re-run the gate.** `make pre-commit` is the check that the merge left something coherent.

**If you deleted an entry from `ddl.py`, read this before updating.** Deleting one used to be
silent. It is not any more: every database that applied that entry reports a key your build no
longer carries, so `check` refuses to serve it and `make schema` refuses to forget it. Both name
the key. Either put the entry back, or, if you are certain no database ever ran it, delete its
row from `applied_once` and its line from `backend/.schema-baseline.json` in the same commit.
Deleting the demo `tasks` entries after migrating a database with them is the usual way into
this. [docs/schema.md](schema.md) says what the baseline is and why it refuses.

If you never intend to take template updates, delete `.copier-answers.yml` and the question
stops arising. That is a legitimate choice; making it deliberately is the point.
