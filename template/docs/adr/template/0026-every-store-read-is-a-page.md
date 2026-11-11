# 0026. Every store read is a page

Date: 2026-11-06

## Status

Accepted.

## Context

In a project generated from this template, one person's requests held the event loop for 189 s
of CPU in an hour, on an instance with one vCPU. 135 s of it was one store function. It read every
row of a document, 6,500 rows and 5.6 MB, and validated each into a model on each read. The
callers then picked one row or one page from it. Each read used about 80 ms of CPU on a Mac and
about 0.4 s in production.

The tests did not see it. The event-loop limit in `tests/event_loop.py` measures each step, and
a step costs what the data costs. The fixtures held a few rows, so the same code passed in the
tests and held the loop for 0.4 s to 2 s in production. A cost that grows with the data passes
every test whose data is small.

The template had the same shape. `TaskStore.list` and `AuditEvents.list` answered every row of
the tenant, and `GET /tasks` answered every task. The example is the code an agent copies.

## Decision

- **No store method answers more than one page.** A page holds at most `PAGE_MAX` rows, 100,
  and at most `PAGE_BYTES` bytes, 1 MiB, both in `app/models/shared.py`. It always holds its
  first row. A list takes `after`, and answers a page and `next`. `next` names where the page after this one starts, and is `None` on the last page.
  `TaskPage.tasks` has `max_length=PAGE_MAX`, so `openapi.json` carries the bound as `maxItems`,
  and a route that answers more fails with a `500`.
- **The database does the picking, and one function reads every page.** `fetch_page` in
  `app/store/pages.py` runs one `LIMIT` of `PAGE_MAX + 1` rows, on the index that leads with
  `tenant_id` and ends with `seq`. In the same statement, Postgres adds up the bytes of each row,
  `octet_length(row::text)`, and drops each row after the one that passes `PAGE_BYTES`. So a row
  that the page does not answer never leaves the database. The function knows from the rows it
  read whether a next page exists. No code reads a whole list to answer part of it.
  `tests/store/test_pages.py` fails on a read of many rows anywhere in `app/` but `fetch_page`,
  except in a module that `ELSEWHERE` names with the reason its rows are bounded.
- **A page fits its first row.** The bounds on one row's columns add up to at most `PAGE_BYTES`,
  [adr/template/0031](0031-the-database-bounds-the-bytes-of-every-value.md) says how, so no row is
  too wide for a page of its own.
- **Each request counts the pages it reads.** `fetch_page` counts each page on the request's
  `request completed` line, as `pages_read`. A route that reads every page reads one page for
  each `PAGE_MAX` rows, so the count grows with the data, and an alert on the line sees it.
  `test_a_request_line_counts_the_pages_a_list_read` reads `GET /tasks` at one row a page.
- **Tasks are newest first.** A task that a person creates is on the first page, so the screen
  shows it at once, whatever the tenant holds. Audit events stay in the order they were recorded.
- **A cursor names a row, never a position.** The `after` of a task page is the id of the last
  task on the page before. A position would skip or repeat a row when a row before it is removed.
  The database pages from that task's `seq`. The cursor is not `seq`, because `seq` is one
  sequence for every tenant, and its numbers would tell a tenant how much the others write.
- **A cursor that names no row is reported.** `TaskStore.list` answers `None` when `after` names
  no task of the tenant: removed, never there, or another tenant's. `GET /tasks` answers it with
  `404`. The route never answers an empty page for it, because an empty page is what the end of
  the list looks like. `AuditEvents.list` raises `NoSuchAuditEvent`, because no route reports it.
- **The tests hold both substrates to the bound, and hold Postgres to the `LIMIT`.**
  `tests/store_contract.py` writes `PAGE_MAX + 1` rows in a new tenant on each substrate. It
  asserts that the first page holds `PAGE_MAX`, that `next` reads the rest, and that the order
  holds across the pages. It asserts that a cursor that names no row is reported.
  `tests/integration/test_pages_on_postgres.py` counts the rows that each `fetch` returns, and
  fails on a read that fetches more than `PAGE_MAX + 1`. It lowers `PAGE_BYTES` and asserts that
  a page stops at it, that a row wider than it is a page of its own, and that `next` reads the
  rest. The bound comes from the code, not from the size of the data, so it holds for a fixture
  of any size.
- **The mock pages as the backend does.** `src/mocks/store.ts` holds its own `PAGE_MAX`, and
  `devtools/check_template.sh` fails when it differs from `maxItems` in `openapi.json`.
- **The screen reads one page on its first draw, and the next page on a person's click.** The
  task list is a `useInfiniteQuery`. "Load more" reads the page that `next` names. After a read
  of a page fails, "Load more" reads every page again from the first. The first draw still costs
  one read, as `tests/read-budgets.ts` says.
- **A test that needs a whole list reads it page by page.** `every_task` and `every_event` in
  `tests/pages.py` follow `next` to the end. A route never does.

## Considered options

- **Count the rows each request decodes, and hold it to a budget.** This depends on the fixture.
  A read of a whole list that answers one row decodes one row from a one-row fixture, and passes.
  It also passes a read that answers every row, because each decoded row is answered.
- **Time each path at N rows and at 10N rows, and fail when the cost grows.** A timing at the
  ratios that matter is noisy. It covers only the paths that somebody wrote it for, and each one
  needs a fixture ten times the size.
- **A `limit` that the client sends.** Nothing in the app needs a smaller page. One fixed bound
  is less code, and is the same on every call.
- **Measure the bytes of a page in Python, after the fetch.** asyncpg builds the whole result
  before `fetch` returns, so the bytes are already in the process when Python counts them.
- **A smaller `PAGE_MAX` for a collection of wide rows, from the bounds on its columns.** Most
  rows are much smaller than their bound, so each page would hold a few rows where it could hold
  `PAGE_MAX`.
- **`OFFSET`.** It reads and drops every row before the page, so its cost grows with the page
  number. A row that is removed before the offset moves every later row by one.
- **`seq` as the cursor.** It discloses the writes of other tenants, as above.
- **Oldest first.** A task that a person creates goes on the last page. When the screen has not
  read that page, the task does not appear, and nothing tells the person why.
- **An exception for the audit events, which no route serves.** A read with no bound is where
  the next unbounded read starts. The audit cursor is the event's `seq`, because an audit event
  has no id. Give the events an id before a route serves them.

## Consequences

- `GET /tasks` answers `{tasks, next}`, newest first, not a list. A client of the old shape
  breaks.
- A focus, and a mutation of the key, read each page the screen holds again, one request after
  another. The read budget counts only the first draw, so it does not count these. One actor's
  burst admits `LIMITS.burst` requests, so a screen that holds more pages than that meets a
  `429`.
- A read after a task that another person removed answers `404`. The screen draws the error,
  and the next click on "Load more" reads from the first page.
- A read that needs every row is not a list. A count, a sum or a group belongs in SQL. A value
  that only all the rows give is derived once per version of its rows, outside the request.
