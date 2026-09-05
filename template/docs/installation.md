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

`make`, `git` and `python3` complete the set; all three ship with the Xcode command line tools
on macOS and with build-essential and the base system on Debian and Ubuntu. `python3` is not
the application's Python — the one uv installs is. `devtools/dev.sh` and
`devtools/contract-test.sh` use it to put each half in a process group of its own, which is the
only way they can stop what they started.

## Install

```bash
make install
```

That runs `pnpm install` in `frontend/`, `uv sync --all-groups` in `backend/` (which installs
the Python version pinned in `backend/.python-version`), and activates the versioned git hooks.

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
uvx --exclude-newer "14 days" copier@9.17.1 update
make install
make openapi
make schema
make pre-commit
```

Copier resolves against **tags**, so `update` brings you to the template's newest release
rather than whatever is on its default branch. It is a three-way merge against the commit you
generated from, so commit or stash first.

What to expect, because it is not all free:

- **Files you never touched update cleanly** — tooling, gates, `Makefile` targets, docs, ADRs.
- **Files you rewrote come back as conflicts.** `app/routes.py` and `app/models.py` are the
  first two any real project replaces. You are porting a pattern rather than accepting a patch.
- **Regenerate the contract afterwards.** `openapi.json` and `frontend/src/api/schema.ts` are
  generated from your code, so merging them is meaningless.
- **Regenerate the schema artifacts too.** `deploy/schema.sql` and `backend/.schema-baseline.json`
  are generated from your `ddl.py`, which by then holds your entries as well as the template's.
  A merged copy of either describes neither project, and `make pre-commit` says so. `make schema`
  rewrites both. Commit them with the update.
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
