# 0004. The example is declined on update, and never on copy

Date: 2026-10-05

## Status

Accepted.

## Context

The template ships an example resource, `tasks`. The rest of the template is built on it: the
store protocol, the schema and its row-level policy, the store contract, the frontend and
`openapi.json`. A real project replaces the example, often with its own resource at the same
paths. Each `copier update` then delivers the example again, as a conflict in every file the
project rewrote. These are the largest conflicts in an update.

The identity stub has the same problem. `app/identity.py` resolves every request to the sentinel
tenant. The routes and the boot log are built on that seam, as
`template/docs/adr/template/0004-a-route-cannot-escape-the-identity-seam.md` says. A real project
replaces the stub with real authentication. The tests of the stub in the template then conflict
with the tests of the project on every update.

Copier gives a validator no access to the operation, so a question cannot refuse an answer on a
copy only. `_exclude` does get the operation, as `_copier_operation`.

## Decision

- `copier.yml` asks two questions, `example_resource` and `identity_stub`. Both default to true.
- When an answer is false, `_exclude` in `copier.yml` drops the files that question owns, and
  only when `_copier_operation` is `update`. `_exclude` is the one list of those files.
- A copy always ships every file. The help text of each question asks only about
  `copier update`.
- A project records a false answer in `.copier-answers.yml` and commits it before an update.
  An update renders the old version with the last answers. A false answer given on the update
  itself leaves the owned files in the old render and out of the new one, and Copier deletes a
  file the new render no longer has, edited or not. `template/docs/installation.md` and the help
  text of each question say this.
- `devtools/check_template.sh` renders a copy with both answers false. It reads the owned files
  out of `copier.yml`, and it refuses the copy if any of them is missing or if either answer is
  not recorded.
- `devtools/check_template.sh` also updates a copy that recorded both answers false and edited
  every owned file, across a later template commit that changes every owned file. It refuses
  the update if an owned file is gone, no longer holds the project's edit, or took the
  template's change.

## Considered options

The failures of the first two options come from a real project's update, not from reasoning.

**`_skip_if_exists` on the files of the example.** It removes the conflicts. It also adds back
each file the project deleted, because Copier renders a skip-listed file whenever it is missing.
A project that deletes the example then gets it back on every update. Rejected.

**A question whose false answer excludes the files, with a validator that refuses false on a
copy.** The validator cannot see the operation, so the copy goes through. The result is a project
whose `backend/app/models/__init__.py` imports a file that is not there. Rejected.

**Make the example fully optional.** Every file that mixes framework and example splits in two,
so a copy without the example still runs. That turns the store protocol, the schema and the
frontend into conditional shapes, for a choice only an existing project makes. Rejected for its
size.

## Consequences

- A project that records false before an update keeps what it has at the owned paths. An update
  never adds back a file the project deleted.
- A project that gives false on the update itself loses the owned files once. It gets them back
  from its own history.
- The files that mix framework and example keep updating. They include the store, the schema,
  `backend/app/models/__init__.py` and `frontend/src/mocks/handlers.ts`. A project still meets
  the example in those files.
- The answer has no effect on a copy, so it is safe to ask there.
- The gate checks the update half against a later commit of the working tree, not against a
  released tag. A change in how Copier resolves a tag is outside what it proves.
