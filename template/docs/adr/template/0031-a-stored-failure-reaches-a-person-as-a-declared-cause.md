# 0031. A stored failure reaches a person as a declared cause, never as a message

Date: 2026-11-11

## Status

Accepted.

## Context

The template guards each failure that a request raises.
[0006](0006-a-refusal-is-a-class-declared-once.md) makes each refusal a class with a declared
sentence. An unhandled failure answers `Internal server error`. Each guard sits on a raise.

A failure that work stores is data. A route reads it from a row and answers `200`, so nothing
raises and no guard sees it. The template's own `Job.failure` was a free `str`. A project followed
that example. Its screen showed a person an engine's own words: the class of the error, the name of
a step, the id of a run, and a count.

An error's message is written for the person who reads the code. It names the parts of the system.
It can name another tenant's data. It changes whenever the engine changes.

## Decision

- **A failure that a response carries is a `Failure`.** It holds `cause`, a name from an enum, and
  `said`, the sentence that the cause declares. `said` is computed from `cause`, so no caller can
  put other text in it. The backend owns the words, as for a refusal. The frontend draws `said`
  and picks a mark by `cause`.
- **Each cause is a class.** It subclasses `Failing` from `app/failing.py`, and declares `cause`
  and `said`. Work raises it where it fails. A port to another system raises one for each failure
  of that system that a person must read. The project's causes are in `app/failures.py`, as
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) says. The
  template declares two: `internal`, for any error that declares no cause, and `attempts_spent`,
  for a job whose last attempt ended with no outcome.
- **`Failing` refuses a declaration it cannot read.** A subclass outside `app/failing.py` and
  `app/failures.py`, a cause that is not snake_case, a cause declared twice, and an empty sentence
  each raise `UndeclarableFailure` at import.
- **A row stores the cause, and the log stores the error.** The job boundary stores
  `cause_of(error)`. The line `job failed` carries the error and its traceback. The line
  `job dead` names the cause and the class of the error.
- **Reading a cause that no class declares raises.** A `Failure` with such a cause fails validation. To retire a
  cause, stop raising it and keep its class for one release. In that release, rewrite its rows in
  an entry of `ddl.py`. Each tenant table is under `FORCE ROW LEVEL SECURITY`, and the release
  step runs as the table's owner, so the entry sets each tenant in turn, or it changes no row.
  Delete the class in the next release.
- **A sentence says what happened in plain words.** It names no part of the system, no count and no
  instruction. The screen offers the action.
- **A response field named for a failure is a `Failure`.** `error`, `failure`, their plurals, and a
  name that ends in `_` and one of them are reserved. `tests/failures/test_failure_fields.py`
  reads the contract that the app serves. In each schema that a `2xx` response reaches, it fails
  on such a field unless the field holds a `Failure`: alone, nullable, or in an array. Request
  bodies are out of scope: a client names its own failures, as `ClientEvent` does.
- **A project moves to this rule one field at a time.** `STILL_FREE_TEXT` in `app/failures.py`
  names each `Schema.property` that is free text today. The test passes each one listed. It fails
  on one not listed, and on one listed that no longer holds free text. So the list only shrinks.
- **A refusal never says an error it caught.** `tests/errors/test_errors.py` fails on a refusal
  built inside `except ... as name` from an argument that reads `name`.
  `STILL_SAYS_A_CAUGHT_ERROR` in `app/failures.py` counts such refusals in each module today. The
  test fails on a module over its count, and on a count above what the module builds. So the
  count only falls.
- **A job row that an earlier release wrote reads as a cause.** A release before 0.76.0 stored the
  class of the error in `jobs.failure`. `stored_failure` in `app/store/jobs.py` reads a class name as
  `attempts_spent` for `AttemptsSpent`, and as `internal` for any other. A rolling deploy runs a
  worker of the earlier release beside the new one, and a rollback returns to it. Either can end a
  job after the new release starts, so the translation stays. A class name is spelled in
  CapWords, and a cause in snake_case, so the two never meet.

## Considered options

- **The frontend maps a cause to its words.** Two halves then own the words of one failure, and
  the mocks and the backend can disagree. A refusal already puts its words in the backend.
- **A closed type alone, with no check on names.** A field that never uses `Failure` passes. The
  incident had nine such fields.
- **A check on names alone.** A `Failure` built from an error's message passes it. The closed type
  is what keeps the message out.
- **A frontend component that draws only a `Failure`.** No screen of the template draws a failure.
  The generated type has no field but `cause` and `said`, so a component adds nothing to it.
- **A rule on each `state` enum that holds `failed` or `dead`.** It finds a failure under another
  name, such as `reason`. It also refuses a state that has no cause to show. The check on names
  and the closed type are enough for now.
- **An operator reads the engine's words in a response.** An operator reads them in the log, and
  in the engine's own tools.
- **An entry of `ddl.py` rewrites the old rows once.** A worker of the earlier release writes a
  class name after the rewrite, during a rolling deploy or after a rollback. Each read of that job
  then raises. A rewrite in a second release, after a reader that takes both, needs two releases
  to reach what the reader alone does.
