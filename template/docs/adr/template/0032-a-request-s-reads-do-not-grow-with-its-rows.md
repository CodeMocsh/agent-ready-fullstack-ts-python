# 0032. A request's reads do not grow with its rows

Date: 2026-11-14

## Status

Accepted.

## Context

In a project generated from this template, the home screen read a page of 25 projects. Its one
request made 1,005 round trips to Postgres, in 172 transactions. The database used about 20% of
its CPU. Each round trip took about 3.7 ms. The request took 0.93 s at the median.

The screen found the facts of each project with store calls for that project: about 7 calls for
each. Each store call is one unit of work, and a unit costs 6 round trips: the actor on the lent
connection, `BEGIN`, the tenant, the query, `COMMIT`, and the reset of the connection. So the
cost of the request grew with the rows of the page.

[0026](0026-every-store-read-is-a-page.md) bounds the rows of one read, and not the reads of one
request. Its test counts the rows of each read. A read for each row returns one row, so the test
passes it.

The project tried to start the reads of each project at once. One page then took 48 connections of
the pool, where it took 4 before. A pool holds the share of one process
([0022](0022-the-connections-of-every-process-fit-inside-max-connections.md)), so one person's
page could hold every connection of a process.

## Decision

- **A set read reads one kind of fact for many keys in one statement.** A `SetRead` in
  `app/store/loads.py` takes a connection inside the tenant's transaction and at most `PAGE_MAX`
  keys. It answers one value for each key, as `= ANY($1)` does. A fact of many rows for each key,
  such as the tasks of each project, is an aggregate in SQL: a `GROUP BY` of the key. It is never a
  page for each key.
- **A loader joins the reads that the items of one request start in one turn of the event loop.**
  The code for one item calls `Loader.load(read, key)`. The loader holds each key that is asked in
  the turn. When the turn ends, it runs each set read of the turn once, for every key asked of it.
  A key asked twice in a turn is read once. More than `PAGE_MAX` keys go a page of keys at a
  time. The items of a page start in one `asyncio.TaskGroup`, so each asks in the same turn.
- **A turn is one unit of work, and a loader runs one turn at a time.** Each kind of the turn
  runs, one after another, in one `scoped` transaction. A turn waits until the turn before it ends.
  So one loader holds one connection at a time.
- **A turn is shared work.** The loader runs each turn through `SharedWork`
  ([0021](0021-each-wait-in-a-request-has-a-bound-under-the-request-budget.md)). A failure raises
  in each caller of the turn. When each caller is cancelled, the turn is cancelled, and its
  transaction rolls back. `cancel_beside` cancels a turn at shutdown.
- **A failure of one key's value fails that key only.** Work for many items catches a failure at
  the item, as `AGENTS.md` says, so a set read answers that failure for its key: an exception,
  such as a row that this build cannot read. `Loader.load` raises it in the callers of that key,
  and each other key answers its value. A set read raises only when its statement fails. That
  fails every caller of the turn, as `NotAnswered` and `DatabaseUnavailable` do.
- **A set read answers each key it is handed, and only those.** The loader raises `NotAnswered`
  in each caller of the turn when a set read answers no value for a key, or a value for a key it
  was not handed. A fact that a key may not have is a value that says so, such as `None` or `0`.
- **A loader is for one request and one tenant.** `PostgresDatabase.loader(tenant_id)` answers
  the loader of the tenant in the scope of `loading`. Each HTTP request is one scope:
  `LoadsPerRequest` opens it. A part of `GET /reads` is a request of its own
  ([0029](0029-the-reads-that-start-in-one-task-are-one-request.md)), so it is a scope of its
  own. Each attempt of a job is one scope: `app/worker.py` opens it. Outside a scope,
  `loader_of` raises `NotLoading`.
- **A loader keeps nothing past its turn.** A read in a later turn reads the database again. So
  a write in the request needs nothing to clear.
- **Every GET route reads as much at one row as past a page, on Postgres.**
  `tests/integration/test_routes_read_a_fixed_count.py` fills one tenant with one row in each
  table, and one with `ROWS`, past a page. It reads each GET route in `openapi.json` in each
  tenant. For each request it counts the statements and the connections taken on pooled
  connections, and the most connections held at once. It fails when a route makes more statements
  or takes more connections past a page than at one row. It fails when a request holds more than
  one connection at once. The count is at the connection, as in 0026, so a new store method cannot
  escape it.
- **A route that does not meet this today is named, with its reason, until it does.**
  `GROWS_WITH_ROWS` and `MORE_THAN_ONE_CONNECTION` in `tests/integration/filled.py` each map a GET
  route template to its reason. The template ships both empty. Each has the rules of
  `OVER_A_PAGE`: the test fails on a route over the rule that the list does not name, on a listed
  route that meets the rule, on an entry that is no GET route that the test reads, and on an
  empty reason. `filled` takes the number of rows to write, as
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) says of a list.
- **The test holds itself to its purpose.** A second test adds three routes. One reads the title
  of each task of a page alone. One starts those reads at once in a `TaskGroup`. One reads them
  through the loader. The test asserts that the first two grow with the rows, that only the second
  holds more than one connection, and that the third reads as much past a page as at one row.

## Considered options

- **`aiodataloader`.** Its loader reads the event loop when it is made, and `get_store` is a
  `def` dependency, which runs in a thread with no event loop. It starts the read of each turn
  with `ensure_future` and keeps no reference to it, which 0021 refuses. `cancel_beside` does not
  see it. A cancelled read leaves each caller waiting with no end, and a cancelled caller does not
  cancel the read.
- **A loader for each store, or for each kind.** A request that uses several stores holds a
  connection for each in one turn. The project above uses many stores in one request.
- **A cache of each value for the whole request.** The rule of a constant count needs only the
  keys of one turn. A cache must forget what a write changes. A trigger, a job and another store
  also write, and nothing could check that each one clears the cache.
- **A refusal value for a key, which the caller checks.** A caller that does not check it passes
  the refusal on as a value. An exception for the key raises in its caller, and code that catches
  a failure at its item today catches it the same way behind a loader.
- **A set read on each store port, which the route calls with every key.** Each screen must then
  collect the keys of its items before it reads, and code for one item becomes code for a set.
  The loader keeps the code for one item, and reads its keys as a set.
- **One unit of work for the whole request.** It removes the 6 round trips of each store call,
  and it changes `scoped`, the unit that [0017](0017-the-database-records-every-write-and-the-request-layer-every-request.md)
  records, and what `ConnectionLost` means in 0022. It is a decision of its own. One unit for
  each turn is the part of it that reads need.
- **Compare a page with ten pages.** A route reads one page, so its reads are the same at both,
  even when it reads each row alone.

## Consequences

- A page of items costs one statement for each kind of fact, in one transaction for each turn.
  The count grows with the depth of the reads: a read that needs an earlier read's answer is a
  later turn.
- An item that awaits something else before it asks the loader asks in a later turn. A loop that
  awaits each item before it starts the next reads one key in each turn. The test sees each, as
  more statements past a page.
- A read that a route makes outside the loader, at once with a turn, takes a second connection.
  The test sees it as more than one connection at once.
- A project's set reads are SQL on Postgres. The memory substrate answers each item from its own
  rows, and needs no loader. A route test on the memory substrate does not see a read that grows.
- Work beside the requests that a request starts uses the loaders of that request.
- A project changes `filled` to take `rows` when it updates. The update does not write
  `tests/integration/filled.py`.
- The count does not see a read on a connection outside the pool, such as `dedicated()`. A
  review finds it.
