# 0029. The reads that start in one task are one request

Date: 2026-11-07

## Status

Accepted.

## Context

`AGENTS.md` says that a screen's first draw is one request. 0019 counts the reads of each draw, but
the template gave no way to meet the rule. Each section of a screen reads its own resource, so the
reads of a screen grow with its sections.

A project generated from this template measured 37 screens. Their first draws sent 240 reads.
155 of them were the same shell reads on almost every screen. A focus read every mounted section
again. The largest budget was 29, so `in_flight` was 32. With a pool of 5 connections, the draw
of one person could hold every connection of a process.

The same project grouped the reads of each draw by the task of the browser that started them.
The 240 reads became 67 groups. 29 of the 37 screens needed 2 groups or fewer. On a focus,
nearly every screen read in one group, its largest screen 19 reads in one.

## Decision

- **`request` sends the GETs that start in one task as one request.** `request` in
  `src/api/client.ts` holds each GET to a path that `src/api/parts.ts` names, when the call passes
  no option but the method. The first one it holds starts a timer of 0 ms. When the timer fires,
  `request` sends every GET it holds as `GET /reads?part=<path>&part=<path>`: the reads of that
  task, and of each task that ran before the timer. One path alone goes direct. The same path twice
  is one part. Each call gets the answer it would get alone: its body, or the `ApiError`, `Held` or
  `Unreached` that it would throw. A refusal of `/reads` itself is the answer of each part. The
  group forms below each `queryFn`, so query keys, `meta.invalidates`, `invalidateForServerEvent`
  and each cause of [0024](0024-every-read-has-an-outside-cause.md) are unchanged. A focus, a server
  event and a mutation that invalidate several keys send one request too.
- **A part is a route that declares it.** A GET route passes `openapi_extra=PART` from
  `app/reads.py`. `openapi.json` names it with `x-part`. `make openapi` writes the template of
  each part route to `src/api/parts.ts`, and the gate diffs that file with the other contract
  artifacts.
- **A part waits on nothing outside the database.** In a part, `waiting` raises
  `PartWaitsOutside` before the wait starts. A route that waits on another system does not
  declare `PART`, and answers alone.
- **Each part is a request of its own, in the process.** `GET /reads` sends each part through the
  whole app: every middleware, the identity seam, admission, the request event, the request line
  and the byte bound. Each part carries the headers of `/reads` and its request id. Each part takes
  its connection and gives it back, as alone. So one `/reads` holds at most `part_concurrency`
  connections, and none between parts. `/reads` answers a list of parts in the order of the
  paths: `answered` with the status, the headers and the body, or `left` with a reason. A
  `/reads` that names more than `PATHS_AT_MOST` paths, 256, is refused with `422`. `make openapi`
  writes that number to `src/api/parts.ts`, and `request` starts a second group past it.
- **A part that cannot answer in its group leaves it, and the client sends it again alone.** A part
  leaves when it is not a part route (`not a part`), when it is past `max_parts` (`past the cap`),
  when it takes more than `part_bound_seconds` from its turn (`timed out`), and when its answer does
  not fit under the ceiling of the response (`too large`). Each later part keeps the room to leave.
  A `/reads` whose ceiling cannot name each of its paths as a part that left is refused with
  `GroupTooLarge`, a `422`, so a group never passes the ceiling. One line `part left its group`
  names each route and reason, with the number of parts. A part that leaves on its bound is
  cancelled, and writes `request cancelled`, not `request completed`. The client sends a part that
  left once more, alone, in the same call, and never while a `Retry-After` holds. It is the same
  read under 0024. This is the one retry of a read that reached the server. `readsOf` counts the
  second request, so a part that always leaves fails its budget.
- **A part that raises fails its group.** The error passes out of `/reads`, which answers `500`,
  and each part's call throws that `500`.
- **The project sets the numbers, and a deployment may replace each one.** `LIMITS` in
  `app/read_limits.py` is the project's list, as
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) says. The
  template ships a bound of 0.5 s, 16 parts, and 4 parts at once. `READS_PART_BOUND_SECONDS`,
  `READS_MAX_PARTS` and `READS_PART_CONCURRENCY` each replace one. `wiring.build_reads` refuses to
  start on a count that is not a whole number of at least 1, and on a bound that is not a finite
  number of seconds above 0. It also refuses when `max_parts` parts, `part_concurrency` at a time,
  may take as long as `REQUEST_BUDGET_SECONDS`
  ([0021](0021-each-wait-in-a-request-has-a-bound-under-the-request-budget.md)), when
  `max_parts` is more than the `burst` of admission, and when `part_concurrency` is more than its
  `in_flight`. The line `reads` names the numbers in force.
- **Admission charges each part.** Each part takes a place of `in_flight` and one request of the
  rate, as alone ([0023](0023-the-server-admits-each-actor-s-requests-within-a-bound.md)).
  `/reads` itself is public, and takes nothing.
- **The byte bound holds each part, and the group.** Each part has the default bound of
  [0028](0028-every-finite-response-has-a-byte-bound.md). `/reads` declares the ceiling. It counts
  each part in the UTF-8 bytes it sends, not in characters, so a group of non-ASCII parts stays
  under the ceiling, and a part that would pass it leaves as `too large`.
- **A part never carries a cookie.** A part's headers are JSON that the client reads, so a
  `set-cookie` there would expose an `HttpOnly` cookie. `/reads` drops `set-cookie` and
  `set-cookie2` from each part. A session that a part renews is renewed on the next request that
  is not a part.
- **`src/mocks/` answers `/reads` through its own handlers.** It answers each part with MSW's
  `getResponse`, so a part is not a request that `readsOf` counts. A part whose handler answers a
  network error leaves its group, so that alone it fails with `Unreached`, as against a server.
- **A budget is exact, and a budget above 1 has a reason.** The type `ReadBudget` asks for
  `because` with a number above 1. `tests/first-draw.test.tsx` fails when the first draw sends more
  requests or fewer than its budget. It fails a draw that reads one URL, its query included, in
  two requests. `readsOf` lists the parts of each `/reads`. It settles at a fixed point: each check
  first flushes React in `act`, and the quiet time starts only after a flush that starts no read.
- **`devtools/check_template.sh` holds the group to its purpose.** It adds a second query to
  `TaskList`, beside the first, and expects a first draw of 1. It makes `request` send each GET
  alone, and expects 2. It makes the second query wait for the first answer and expects 2. It mounts
  the second query after a render that React slices, and expects 2.

## Considered options

- **A read for each screen.** One GET for each screen, whose `queryFn` writes the data of each
  section into the section's key. A focus, a server event and a mutation still read each section
  through its own `queryFn`, so a focus reads N. Each screen is a new operation in the contract,
  and a section that moves to another screen changes both operations.
- **A session read that carries the shell.** The shell stays mounted across a navigation, and
  [0020](0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md) reads no
  mounted query again on a navigation. The shell already reads only on a cold load and a focus,
  and its reads go in the screen's group.
- **One connection for every part of a `/reads`.** It holds the connection while the next part
  waits its turn, and across a wait in a part. It also changes each store adapter.
- **Every part at once, or one at a time.** With 16 parts of `GET /tasks` on Postgres, one
  `/reads` took 546 ms one at a time, 542 ms two at a time, 224 ms four at a time, and 176 ms
  sixteen at a time. Sixteen separate requests at once took 201 ms. Four at a time is close to the
  separate requests, and holds a quarter of their connections.
- **A part over its bound fails.** A read that is slow under load then draws an error where it
  drew a slow screen before.
- **A refusal for a path that is not a part, or for a group over the cap.** The client would need
  the cap and the list of parts of the server it talks to. A tab that runs an old bundle
  ([0025](0025-a-tab-learns-of-a-new-build-and-reloads-at-the-next-navigation.md)) has an old
  list. A part that leaves goes out alone and still answers.
- **A streamed answer, one line for each part.** A slow part then delays no other part. But a part
  waits on nothing outside the database, and a part past its bound leaves. A stream needs the
  stream rules of 0023 and 0028, and a draw that `readsOf` can settle. Reopen it when a part that
  only reads the database is measured over its bound.
- **A part unless a route declares that it is alone.** A route that its author forgets to mark
  then delays every screen that reads it, and nothing fails. A route that its author forgets to
  declare a part fails the read budget.
- **A budget that the gate lowers.** A gate that writes its own baseline drifts. The count is the
  same on each run (0020), so an exact budget fails only on a real change.
- **An evaluate mode for the part bound.** A part over the bound does not fail, and
  `part left its group` already names each one.

## Consequences

- The reads of a screen that start in one task are one request. A read that needs another read's
  answer is a second request. A route `loader` starts in one task the reads that need only the
  route's parameters.
- A `/reads` takes as long as its parts, `part_concurrency` at a time. A part that leaves costs
  its screen one more request, and the server's work on it until it left.
- A change that removes a read changes `tests/read-budgets.ts`.
- `in_flight` no longer needs to hold the largest draw: one draw takes at most `part_concurrency`
  places, and a screen's parts take one place each in turn. `burst` still needs the largest draw's
  parts, because each part is one request of the rate.
- A part in `asyncio.to_thread`, or a wait that does not go through `waiting`, is not stopped.
  Review it as a wait outside the database.
- A request log shows `/reads` and a line for each part. Each part's request event names its own
  route, with the request id of `/reads`.
- A part answer is text inside JSON, so a group's bytes are a little more than its parts' bytes.
- `src/mocks/` answers every part, and knows no cap and no bound. A draw past `max_parts` shows
  its second request only against the backend.
- The template's contract suite sends more parts at once than the shipped burst. It ships
  `ADMISSION_BURST=60` in `devtools/contract-test.env`.
- A project without the example has no `src/api/parts.ts` entry for `/tasks`, and does not get
  `tests/api/group-of-reads.test.ts` or `tests/api/reads.contract.test.ts` on an update. Its own
  part routes fill `src/api/parts.ts` at `make openapi`.
