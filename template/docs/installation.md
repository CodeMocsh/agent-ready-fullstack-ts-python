# Installation

Two toolchains, one per half.

## Prerequisites

**Node 24+ and pnpm**, for the frontend half:

```bash
brew install node                                     # macOS
corepack enable && corepack prepare pnpm@latest --activate
```

**uv**, for the backend half. It installs Python itself, so you do not need a system Python of
any particular version:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`make` and `git` complete the set; both ship with the Xcode command line tools on macOS and
with build-essential on Debian and Ubuntu.

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
- **Re-run the gate.** `make pre-commit` is the check that the merge left something coherent.

If you never intend to take template updates, delete `.copier-answers.yml` and the question
stops arising. That is a legitimate choice; making it deliberately is the point.
