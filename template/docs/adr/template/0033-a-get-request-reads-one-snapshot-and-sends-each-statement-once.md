# 0033. A GET request reads one snapshot, and sends each statement once

Date: 2026-11-21

## Status

Accepted. It replaces two considered options of
[0032](0032-a-request-s-reads-do-not-grow-with-its-rows.md) for the reads of a GET request: a
cache of each value for the whole request, and one unit of work for the whole request.

## Context

A project generated from this template served 8,464 requests in one day at a median of 0.47 s.
Its busiest screen made 63 transactions in one request. The database used about 20% of its CPU,
and each round trip took about 3.7 ms. So the cost was the round trips, not the work of the
database.

The project counted the statements of four GET routes on Postgres, at 1 row and at 10:

| Route | All statements | Statements that read | After an exact repeat is kept | Statement texts |
|---|---|---|---|---|
| `/board` | 45 → 153 | 23 → 77 | 13 → 40 | 11 |
| `/boards` | 53 → 323 | 27 → 162 | 19 → 91 | 10 |
| `/standings` | 75 → 165 | 49 → 121 | 44 → 98 | 18 |
| `/track` | 48 → 48 | 38 → 38 | 30 → 30 | 17 |

Three facts follow:

- About half of the statements set the tenant and who acts. Each store call is one unit of work,
  and each unit sets them again.
- The same statement with the same arguments ran up to 4 times in one request. Keeping each
  answer removes 10% to 48% of the statements that read.
- What remains past the number of statement texts is a read for each item, which 0032 answers
  with a loader.

0032 refused a cache for the request, because a write must clear what it changed, and nothing
could check that each writer clears it. It left one unit of work for the request to a decision of
its own.

## Decision

- **A store method that only reads takes `PostgresDatabase.reading`. A store method that writes
  takes `scoped`.** `reading` is a READ ONLY transaction, so a write through it fails in
  Postgres, in every request and in every test on Postgres. Inside `app.store.jobs.fenced` it is
  not READ ONLY, because Postgres refuses the `FOR KEY SHARE` that holds the claim in a READ ONLY
  transaction.
- **In a GET or HEAD request, `reading` lends the snapshot of the tenant.**
  `app/store/snapshots.py` opens it at the first read of the tenant in the request: one
  transaction, READ ONLY and REPEATABLE READ, on one connection. Each read of one snapshot sees
  the same data.
- **Each read of a snapshot runs on its one connection, one at a time.** A read waits until the
  read before it ends. The items of a page in one `asyncio.TaskGroup` share the connection, so
  the request holds one connection for its reads.
- **A snapshot keeps each answer.** `Kept` answers a `fetch`, `fetchrow` or `fetchval` that it
  already answered: the same method, statement text and arguments, each argument with its type.
  A list, tuple, set or dict is an argument by what it holds. An argument that cannot be kept,
  such as a `bytearray`, raises `TypeError`. The keep is at the connection, so no store method can
  escape it. A statement that calls a function whose answer changes in a transaction, as
  `SENT_EACH_TIME` names (`clock_timestamp()`, `random()`, an advisory lock), goes to the
  database each time.
- **A snapshot ends before the response starts.** `SnapshotsPerRequest` ends each snapshot of the
  request at `http.response.start`, and when the request ends. The request event of
  [0017](0017-the-database-records-every-write-and-the-request-layer-every-request.md) then takes
  the connection after it. A read after the end runs alone.
- **A write in a GET request runs in `scoped`, on a connection of its own, and ends the snapshot
  of its tenant when it commits.** The next read opens a new snapshot, which sees the write and
  keeps nothing from the old one. A write on `connection()` or `lent()` ends it through
  `PostgresDatabase.written`. Inside a read, the read ends the snapshot when it ends.
- **A read that fails in the database ends the snapshot.** One of `failed_on_a_connection()`, or a
  cancel, ends it. The failure raises in its caller only. The next read opens a new snapshot. So
  an item that catches its own failure, as `AGENTS.md` says, does not make the reads of other
  items fail. Another failure, such as a `404` that the code of the read raises, keeps the
  snapshot.
- **A loader turn reads on the snapshot. Other shared work and work beside the requests read
  alone.** A loader is for one request
  ([0032](0032-a-request-s-reads-do-not-grow-with-its-rows.md)), so each caller of its turn is in
  the request. A run of other shared work can be joined by another request, and can outlive this
  one. `waits.in_shared_work` and `snapshots.for_a_turn` tell them apart. The end of a snapshot
  waits for the read in flight, so no read uses the connection after it goes back to the pool.
- **A read, or a wait for a loader turn, inside a read raises `ReadInsideARead`.** The first read
  holds the connection until it ends, so the second would wait for ever. A task that a read
  starts is refused too.
- **A GET route sends each statement text once in a request, on Postgres.**
  `tests/integration/test_routes_read_a_fixed_count.py` records the text of each statement that
  reaches the database, without `SET_TENANT_AND_ACTING` and `SET_ACTING`, and the code that sent
  it. It fails on a route that sends one text more than once, at one row or past a page, and names
  the route, the text and each sender. `REPEATS_A_STATEMENT` in `tests/integration/filled.py`
  names each route that does so today, with its reason, by the rules of `GROWS_WITH_ROWS`. The
  template ships it empty.
- **The test holds itself to its purpose.** A route that reads the first page twice sends what a
  route that reads it once sends. A route that reads each item alone, and one that reads each at
  once, repeat a statement. Only a route that writes while it reads holds two connections at once.

## Considered options

- **A snapshot for every request, a write included.** A write must see the writes that it made,
  and a POST does not read the same thing twice. Writes keep one unit for each store call. 0017
  records each unit, and 0022 counts each.
- **Keep each answer for each store method and its arguments.** It also saves the work of Python
  on each repeated call. Each store method must then say how its arguments key it, and a new
  method escapes the keep until it does. The keep at the connection saves the round trips, which
  are the cost. A project can keep a value above it later.
- **Lend the snapshot only to the task that opened it.** A loader turn runs in its own task, so
  its reads could not use the snapshot, and each item that reads at once needs a snapshot of its
  own.
- **Lend the snapshot to every task of the request.** A request that joins a run of shared work
  that another request started would read the other request's snapshot, without its own writes.
  The other request's response would also wait for the run's read.
- **Keep a snapshot past a write in the request.** A route that asks for a job and then reads it
  would not see the job.
- **A failure ends every later read of the request.** An item that catches its failure would
  make every later item fail with a failure that is not its own.
- **Count the store calls of each request on the memory substrate.** The kept answer makes a
  repeated store call free on Postgres, so the count would name calls that cost nothing. The
  count is at the connection, as in 0026 and 0032.
- **Name a statement by its text with the values left out.** asyncpg sends the values apart from
  the text, so the text already leaves them out.

## Consequences

- A GET request sends 2 round trips to open its snapshot, `BEGIN` and `SET_TENANT_AND_ACTING`,
  one for each statement that it does not keep, and 2 to end it, `COMMIT` and the pool's reset. A
  store call in a snapshot costs one round trip, where it cost 5
  ([0017](0017-the-database-records-every-write-and-the-request-layer-every-request.md)).
- A GET request holds its connection from its first read until its response starts, where it
  gave it back between store calls. A wait outside the database in that time holds it too. Past
  `Timeouts.idle_in_transaction`, Postgres ends the session, and the next read raises
  `ConnectionLost`, a `503`. Size the pool for the GET requests that run at once.
- The wait for the connection of a snapshot has no bound of its own. It lasts as long as the
  reads ahead of it: each `reading` block, and each loader turn, which may send several
  statements.
- `now()` answers the same in the whole snapshot, where it changed with each store call.
- A function of the project's own whose answer changes in a transaction answers the same each
  time in a snapshot. `SENT_EACH_TIME` does not name it.
- A value that a kept read answers is the same object each time. Code that changes it changes
  the next answer.
- A read that a project sends with `execute`, a cursor or a prepared statement is not kept.
- `scoped` ends the snapshot when it commits, even when it wrote nothing. A conditional write
  reads through `reading` first, and takes `scoped` only to write.
- A loader turn of more than `PAGE_MAX` keys for one set read sends its statement once for each
  `PAGE_MAX` keys, and the test names it as a repeat.
- A snapshot that fails to end at the start of the response fails the request with a `500`: the
  response has started, so it cannot become the `503` of `ConnectionLost`.
- The memory substrate has no snapshot. A route test on it does not see a read that repeats, a
  write through `reading`, or a read inside a read.
- A project adds `REPEATS_A_STATEMENT` to `filled` when it updates. The update does not write
  `tests/integration/filled.py`.
- Each part of `GET /reads` is a request of its own, so it has a snapshot of its own. Two parts
  of one group may see different data.
