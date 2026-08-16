# 0001. Copier over a bespoke CLI

## Status

Accepted, 2026-08-16.

## Context

This template has two siblings with two different answers to the same question.
agent-ready-ts ships a bespoke, zero-dependency Node CLI: `render.ts` substitutes a
double-braced token in file contents and path names, plus a `.if-license` filename suffix
for conditional files, and `cli.ts` handles prompts and disk. agent-ready-python uses
[Copier](https://copier.readthedocs.io/) and ships no generator code at all.

Either precedent is defensible here. This template's frontend half is lifted from
agent-ready-ts, so inheriting that repo's mechanics would let frontend changes port across
as plain file copies. Its backend half is lifted from a spike shaped by agent-ready-python's
conventions, so the opposite is equally true.

Three things break the tie, and the first is specific to what this template contains. This
is the most dotfile-heavy tree of the three: `.claude/`, `.entire/`, `.githooks/`,
`.github/`, `.gitignore`, `backend/.python-version`, and three committed `.env` files in the
frontend half. Distribution through `pnpm dlx github:` runs the tree through npm's
pack-and-install machinery, which renames a packaged `.gitignore` to `.npmignore` on
extraction and drops nested ones entirely. agent-ready-ts lives with that: it stores
`template/gitignore` undotted, restores the dot at render time, and pays for a check that
installs a real tarball to prove the rename still behaves. Each dotfile added here would be
another instance of that problem.

Second, agent-ready-ts documents having no update path as tracked debt. Re-running it into
an existing directory overwrites rather than merges, so updating a generated project means
reading the template's diff and applying it by hand. This template will evolve faster than
either sibling — it carries a contract flow and two ecosystems — so the projects it
generates need a real update path more, not less.

Third, the obvious objection to Copier is that it makes the generator depend on a Python
toolchain. That objection does not survive contact with this template: the backend half
requires uv anyway, so requiring `uvx` to generate the project is not a new cost.

## Decision

Pure Copier. `copier.yml` plus `_subdirectory: template`, and no generator code of any kind
in this repository.

Distribution is:

```bash
uvx --exclude-newer "14 days" copier@9.16.0 copy \
  gh:CodeMocsh/agent-ready-fullstack-ts-python my-app
```

which works against a private repository because git supplies the credentials. Releases are
tagged `v0.x.y` from the first one, because `copier update` resolves against tags.

## Consequences

**Positive.** The npm dotfile trap disappears, and with it the undotted storage convention,
the restore step at render time, and the tarball round-trip assertion that existed to police
them. Copier delivers by `git clone`, so a dotfile is a dotfile.

`copier update` comes free. Generated projects record their answers in
`.copier-answers.yml`, which carries the source path and the commit they were rendered from,
so pulling a later template improvement is one command and a conflict resolution rather than
a manual diff.

There is no `src/`, no `package.json`, no `licenses/` directory and no unit-test job in CI.
Conditional files are expressed as Jinja in the filename — the LICENSE file is emitted by a
name that evaluates empty when no license was chosen — and license bodies as a single
if/elif chain, because Copier already has the expression language that `.if-license` and a
`licenses/` directory exist to work around. The repo's own checks collapse to one script.

**Negative.** The mechanics now diverge from agent-ready-ts, which is the sibling this
template shares the most *content* with. A frontend change ported from there cannot be
copied blind: its `{{ token }}` substitution happens to look like Jinja, but its
`.if-license` suffix and its undotted filenames do not exist here and its `.jinja` suffixes
do not exist there. Porting means reading, not copying.

A `.jinja` suffix takes a file out of its own toolchain. The editor stops type-checking it,
the formatter stops touching it, and the file's own linter never sees it. This is mitigated
by a hard rule — no `.ts`, `.tsx`, or `.py` file is ever suffixed, and the backend half is
entirely token-free apart from `pyproject.toml.jinja` — which pushes all variability into
config files and metadata, where losing editor support costs little. The rule has a real
price: anything project-specific a source file needs must be read at runtime rather than
substituted at render time.

The forgotten-suffix failure mode is new and silent. A token added to a file that was never
renamed to `.jinja` ships as the literal text `{{ package_name }}` into every generated
project, and nothing in the rendering pipeline objects. `devtools/check_template.sh` asserts
after every render that no `{{ … }}` survives, no `{% … %}` survives, and no `*.jinja` file
remains on disk, which is the only thing standing between that mistake and a user.

Generation now depends on third-party software this project does not control. Copier is
pinned to an exact version and run behind `--exclude-newer`, so a bad release cannot reach a
user unannounced, but a Copier major with different rendering semantics is a migration this
repo will have to perform rather than absorb.

Finally, this is hard to reverse. Once projects exist carrying `.copier-answers.yml` and
expecting `copier update`, moving to a bespoke CLI would strand every one of them on a
manual upgrade path — which is precisely the debt this decision was made to avoid.
