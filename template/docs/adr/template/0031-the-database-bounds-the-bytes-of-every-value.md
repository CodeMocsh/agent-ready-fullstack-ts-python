# 0031. The database bounds the bytes of every value

Date: 2026-11-10

## Status

Accepted.

## Context

[0026](0026-every-store-read-is-a-page.md) bounds the rows of a page.
[0028](0028-every-finite-response-has-a-byte-bound.md) bounds the bytes of a response. Neither
bounds one row. In a project generated from this template, one row of a version table held a copy
of a whole document, 9.4 MB. Other rows held up to 963 KB. A page of 100 such rows is 100 MB. A
read of one row by its id is not a page at all.

A request model bounds only what the API writes. A worker, a job, a script or an agent writes
past it. The bound has to hold for every writer, so it belongs to the database.

## Decision

- **Every column of variable width has a `CHECK` on its bytes.** The `CHECK` is
  `octet_length(column::text) <= N`, and its name is `<table>_<column>_bytes`. `COLUMN_BYTES` in
  `app/store/ddl.py` declares each bound of the template's tables. Postgres refuses a write over a
  bound, from any writer, with `CheckViolationError`.
- **The bounds on one row add up to at most `PAGE_BYTES`.** So a page always fits its first row.
- **The request model bounds what the API writes, more tightly.** `CreateTaskBody.title` holds at
  most `TITLE_MAX` characters, and four bytes each fit `tasks.title`. `encoded` in
  `app/store/jobs.py` refuses a job's input or outcome over `JSON_BYTES` as `UnstorableJson`.
  So an API write over a bound answers a `422`, and the `CHECK` refuses only a writer that no
  model covers.
- **A bound reaches an existing database without a lock on its reads.** `bound_sql` adds each
  bound `NOT VALID`, in an entry of its own in the band at `BOUND_BAND`. A write from then on is
  held to it at once. The entry at `BOUNDS_VALIDATED_ENTRY_KEY` then validates each bound against
  the rows already stored. A stored value over its bound stops the migration, and the error
  names every bound such a value breaks. The release that needs the bound does not serve until
  the migration completes.
- **A value larger than a row's bound does not go in a row.** A file, or a copy of a whole
  document, goes in object storage, and the row holds a reference to it.
- **The test reads the catalog, so it covers a project's own tables.**
  `tests/integration/test_bounds_on_postgres.py` fails on each column of variable width with no
  valid bound, and on a table whose bounds add up to more than `PAGE_BYTES`. A column whose stored
  values are over the bound it should have goes in `UNBOUNDED_COLUMNS` in that test, with the
  reason. The test fails on a listed column that has a bound, so the list only shrinks.
- **One audited statement writes a bounded number of rows.** An audit event names every row its
  statement wrote, in `resource_ids`, which holds at most half of `PAGE_BYTES`. A statement that
  writes more rows than that names fails.

## Considered options

- **Bound only the columns that a list reads.** Which columns a list reads changes with each new
  route, and no check knows it. The bound would hold only where a review remembered it.
- **A byte bound on the page alone.** A page always holds its first row, and a read by id reads
  one row. Neither is bounded unless the row is.
- **Bound the request models alone.** A worker, a job or a script writes past them.
- **`pg_column_size`.** It measures what Postgres stores, after compression, not the bytes that
  a read sends.
- **Add each bound valid at once.** Postgres then scans the table while it holds a lock that
  blocks every read and write.

## Consequences

- A write over a bound fails. Through the API it answers a `422` where a request model bounds it,
  and a `500` where none does.
- An existing database with a value over a new bound stops at the migration until the value is
  shortened or moved out of the row.
- A statement that writes more rows than one audit event can name fails, and must be split.
- A bound changes only through a new migration entry, because a shipped entry never changes.
