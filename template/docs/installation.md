# Installation

Two toolchains, one for each half.

## Prerequisites

**Node 24+ and pnpm**, for the frontend half:

```bash
# macOS
brew install node
corepack enable && corepack prepare pnpm@latest --activate
```

**uv**, for the backend half. It installs Python itself, so you do not need a system
Python of any particular version:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`make` and `git` complete the set; both ship with the Xcode command line tools on macOS
and with build-essential on Debian and Ubuntu.

## Install

```bash
make install
```

That runs `pnpm install` in `frontend/`, `uv sync --all-groups` in `backend/` (which
installs the Python version pinned in `backend/.python-version`), and activates the
versioned git hooks.

It also writes `frontend/pnpm-lock.yaml` and `backend/uv.lock`, which do not ship with
the template: a project generated today should resolve against today's registry rather
than against whichever day the template was last touched. **Commit both.** CI installs
from them with `--frozen-lockfile` and `--frozen`, so a push without them fails before
it runs anything.

If you only work on one half, install just that one:

```bash
pnpm -C frontend install
cd backend && uv sync --all-groups
```

Neither half needs the other's toolchain to install, lint, test or build.

## Verify

```bash
make lint test
```

The last step starts both halves and runs the contract suite against the real backend,
so a green run means the two agree.

## Updating from the template

This project keeps `.copier-answers.yml`, which records the template it came from and the
exact commit. That is what lets a later template change reach a project generated months ago:

```bash
uvx --exclude-newer "14 days" copier@9.16.0 update
make install
make openapi
make pre-commit
```

Copier resolves against **tags**, so `update` brings you to the template's newest release
rather than to whatever is on its default branch. It is a three-way merge against the commit
you generated from, so commit or stash first.

What to expect, because it is not all free:

- **Files you never touched update cleanly.** Tooling, gates, `Makefile` targets, docs, ADRs,
  and anything the template added since — those arrive without argument.
- **Files you rewrote come back as conflicts.** `app/routes.py` and `app/models.py` are the
  first two any real project replaces, so a template change that reaches into them is a merge
  you do by hand. You are porting a pattern rather than accepting a patch.
- **Regenerate the contract afterwards.** `openapi.json` and `frontend/src/api/schema.ts` are
  generated from your code, so merging them is meaningless — run `make openapi` and commit
  what it writes.
- **Re-run the gate.** `make pre-commit` is the check that the merge left something coherent.

If you never intend to take template updates, delete `.copier-answers.yml` and the question
stops arising. That is a legitimate choice; making it deliberately is the point.
