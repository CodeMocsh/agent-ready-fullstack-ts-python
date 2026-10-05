# The template's decisions are numbered apart from yours

Decisions that ship with the template live in `docs/adr/template/`, numbered on their own. A
project's own decisions live in `docs/adr/`, numbered from `0001`. Code and docs that came with
the template cite `docs/adr/template/NNNN`.

## Why

Both sides number from `0001` and both keep adding. The first project generated from this
template had written its own `0004` onward by the time the template shipped its next decisions,
so a `copier update` landed a second `0009`, a second `0010` and so on beside the project's.
Nothing conflicts as a file, because the names differ, and that is the problem: a docstring the
template shipped, citing its identity-seam decision by number, now names whichever file of that
number the reader opens first.

## Considered options

- **One directory, and the template numbers from somewhere high.** Any range a template picks is
  a range some project reaches.
- **The project renumbers its own on each update.** A decision is never renumbered, because every
  citation of it is a pointer.
- **A prefix in the filename.** A directory says the same thing, and a listing groups it.

## Consequences

- A project that generated before this update has the template's decisions moved under
  `template/`. A citation it wrote of one of them by number needs `template/` added; the link
  check names each one.
- The README in `docs/adr/` is the project's. It says where the template's decisions are.
