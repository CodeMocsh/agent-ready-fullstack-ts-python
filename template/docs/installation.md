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
