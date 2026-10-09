# 0016. The template's decisions are numbered apart from yours

Date: 2026-10-05

## Status

Accepted.

## Context

The template ships decision records, and a project writes its own. Both sets number from `0001`,
and both grow. Numbers are a reading order, so each set is also renumbered when a record in it
merges, goes away or moves. `docs/adr/README.md` states that rule.

In one directory, `copier update` lands a template record beside a project record of the same
number. Nothing conflicts as a file, because the names differ. A citation by number then names
whichever of the two files the reader opens first. A docstring the template ships, which cites
the identity-seam decision by number, then points at a project's decision instead.

## Decision

- **The template's records live in `docs/adr/template/`.** They are numbered from `0001`, apart
  from the project's. A template update renumbers them when it merges, removes or moves one.
- **A project's own records live in `docs/adr/`.** They are numbered from `0001`, apart from the
  template's. The project renumbers them, and no template update touches them.
- **Code and docs that ship with the template cite `docs/adr/template/NNNN`.** A template update
  that renumbers a record updates every citation the template ships in the same change.
- **Each set is numbered from `0001` with no gaps.** In the template's own repository,
  `numbered_without_gaps` in `devtools/check_template.sh` holds both sets in a rendered project,
  and `devtools/links.py` checks that every citation there names a record that exists.
- **`docs/adr/README.md` ships with the template, and an update keeps it current.** It says where
  each set lives and how a record is written.

## Considered options

- **One directory, with the template numbering from a high range.** Any range a template picks
  is a range some project reaches.
- **One directory, with the project renumbering its own records on each update.** Every template
  release that adds a record then forces the project to renumber and rewrite its citations, for a
  change the project did not make.
- **A prefix in the filename.** A directory says the same thing, and a listing groups it.

## Consequences

- A citation the project writes of a template record can go stale. A template update that
  renumbers a record does not rewrite the project's own citations, and nothing in a generated
  project checks them. A project that cites a template record checks the citation after each
  update.
- A project that cites a template record writes `template/` in the path. A citation without it
  names the project's own record.
