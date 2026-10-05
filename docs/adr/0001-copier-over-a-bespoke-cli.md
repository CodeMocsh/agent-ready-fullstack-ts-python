# 0001. Copier over a bespoke CLI

Date: 2026-10-05

## Status

Accepted.

## Context

This template has two siblings, and they answer this question in opposite ways. agent-ready-ts
ships a bespoke Node CLI with no dependencies. agent-ready-python uses
[Copier](https://copier.readthedocs.io/) and ships no generator code. The frontend half here comes
from agent-ready-ts. The backend half follows the conventions of agent-ready-python. So the origin
of the code does not decide the question.

Three forces decide it.

The template holds many dotfiles: `.claude/`, `.entire/`, `.githooks/`, `.github/`, `.gitignore`,
`backend/.python-version`, and the committed `.env` files of the frontend half. Distribution
through `pnpm dlx github:` sends the tree through the pack-and-install step of npm. That step
renames a packaged `.gitignore` to `.npmignore` and drops nested ones. A generator that ships that
way must store each dotfile under another name and restore it at render time. It must also test
that round trip through a real tarball. Each new dotfile adds one more case of this problem.

A generated project needs an update path. A CLI that renders into an existing directory
overwrites it. The user must then read the diff of the template and apply it by hand. This
template carries a contract flow and two ecosystems, so it changes often. Its generated projects
need an update path more than the projects of either sibling.

The usual objection to Copier is that generation then needs a Python toolchain. The backend half
already needs uv, so `uvx` adds no new requirement.

## Decision

- The generator is Copier and nothing else. `copier.yml` holds the questions, their validators
  and `_subdirectory: template`. The repo holds no generator code: no `src/` and no
  `package.json`.
- A conditional file uses Jinja in its filename. The license file is
  `{% if package_license != 'None' %}LICENSE{% endif %}.jinja`. It renders to no file when the
  answer is `None`. The license bodies are one if/elif chain inside it.
- One Copier version is pinned, in `COPIER_SPEC` in `devtools/render.sh`. Every render runs it
  through `uvx --exclude-newer "14 days"`. `devtools/check_template.sh` refuses any other
  `copier@` version that a doc, script or workflow in the repo names.
- A release is a git tag `v` plus the contents of `VERSION`, because `copier update` resolves
  against tags. `.github/workflows/release.yml` cuts the tag when the gate passes on main.
- `devtools/render.sh` refuses a render that leaves a `{{ … }}` token, a `{% … %}` statement
  or a `*.jinja` file in the generated project.
- `devtools/check_template.sh` refuses a generated project whose `.copier-answers.yml` does not
  record `_src_path` and `_commit`. `copier update` reads both.
- The rules for where Jinja may appear are in [../constraints.md](../constraints.md).

## Considered options

**A bespoke CLI, as in agent-ready-ts, distributed through `pnpm dlx github:`.** A frontend
change from that sibling then ports as a plain file copy. The CLI brings the dotfile problem of
npm with it, and it has no update path. Rejected.

## Consequences

- Copier delivers the template by `git clone`, so a dotfile stays a dotfile. There is no renamed
  storage, no restore step and no tarball test.
- `copier update` brings a later template into a generated project as a three-way merge. The user
  resolves conflicts and does not apply a diff by hand.
- A change from agent-ready-ts does not port as a copy. That sibling uses a `.if-license` suffix
  and undotted filenames, and this template uses `.jinja` suffixes. To port a change, read it and
  write it again.
- A `.jinja` suffix takes a file out of its own toolchain. The editor, the formatter and the
  linter stop seeing it. So variability lives in config files and metadata, and the only `.jinja`
  file in the backend half is `pyproject.toml.jinja`. A source file that needs a project-specific
  value reads it at run time. Render-time substitution is not available to it.
- A token in a file without the `.jinja` suffix renders as literal text, and Copier does not
  object. The assertion in `devtools/render.sh` is the only check that stops it.
- Generation depends on software this repo does not control. The pin and `--exclude-newer` stop a
  bad Copier release from reaching a user without notice. A Copier major release with different
  rendering semantics is a migration this repo must do.
- The decision is expensive to reverse. Every generated project carries `.copier-answers.yml` and
  expects `copier update`. A move to a bespoke CLI gives each of them a manual upgrade path.
