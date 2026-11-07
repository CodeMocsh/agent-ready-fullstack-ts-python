# 0021. Each wait in a request has a bound under the request budget

Date: 2026-11-04

## Status

Accepted. [0027](0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md)
replaces its rule for work past the request budget: that work is a job, and `beside` ends within
the budget.

## Context

A project generated from this template had its slow-request alert fire five times in three
weeks for one pattern. Each time, a request waited on a system the project does not own:

- a sandbox attach, 206 s
- an awaited call to another service, 48 s
- a clone and an image build, 57 s, twice
- a login to a customer's SQL Server, which waited out its 15 s timeout and answered `502`

Each wait ran off the event loop, so the rule "blocking work runs off the event loop" passed
each one. No rule said that a request must not wait that long. The fix in each case moved the
work out of the request: the route answers `202`, a task settles the result, and the browser is
told. The project wrote the code that starts that task four times.

The same project also shared work between callers. Several requests awaited one task through
`asyncio.shield`, so a cancelled caller left the task for the others. That task cannot run
beside the requests: a request that joins it waits as long as the work.

## Decision

- **The request budget is `REQUEST_BUDGET_SECONDS` in `app/waits.py`: 10 s.** The deployment's
  slow-request alert uses it as its threshold. It is a constant and not a variable: the gate
  checks each bound against it, and a deployment cannot make the gate check against another
  number.
- **`waiting(seconds, on=...)` is the seam for a wait on a system outside the process.** It
  applies the bound with `asyncio.timeout` and raises `WaitTimedOut`, which names `on`. The
  bound stated and the bound applied are the same number.
- **In a request, `waiting` refuses a bound that is not under the budget.** It raises
  `WaitOutlastsTheRequest` before the call starts. `MarksRequests`, which `assemble` adds, marks
  each HTTP request, so a route test through `tests.routed.routed` is marked too. A WebSocket and
  the lifespan are not marked. The check compares each bound alone with the budget.
- **`beside(work, name=..., within=...)` starts work outside every request.** `within` is under
  the budget, as 0027 says. It clears the mark, holds the
  task, writes `work beside the requests failed` when the task raises and
  `work beside the requests cancelled` when it is cancelled. The task acts as the actor of the
  code that started it. The lifespan calls `cancel_beside` before it closes the database.
  `cancel_beside` raises `BesideOutlivedItsCancel` for a task that does not end within
  `CANCELLED_WITHIN_SECONDS`. The task ends with the result of the work.
- **`SharedWork[K, T]` runs work that several callers await at once.** `joined(key, start)`
  starts one run at a time for each key, and each caller that arrives before the run ends awaits
  the same run. A cancelled caller leaves the run for the others. Each run is in a request,
  whoever starts it, because a request may join it. Each failure writes `shared work failed`, and
  each cancellation `shared work cancelled`. `cancel_beside` cancels a run still in flight.
- **ruff refuses `asyncio.create_task`, `asyncio.ensure_future` and FastAPI's `BackgroundTasks`.**
  Each is in `banned-api` in `pyproject.toml`, and each message names jobs and `beside`. The messages for
  `create_task` and `ensure_future` also name `SharedWork` and `asyncio.TaskGroup`. A task they
  start is still in its request. A loop that the lifespan starts is in a module the project names
  in `per-file-ignores`, because the lifespan is not in a request.
- **The template ships no stream.** The record of the work and its settling are the jobs of
  0027.
- **Postgres is held at start, not through the seam.** `wiring.build_timeouts` refuses to start
  when the answer to a statement, or a connection, may take as long as the budget.
- **The lifespan closes the database within `CLOSED_WITHIN_SECONDS`**, after the cancel, and also
  when the cancel raises.
- `tests/waits/`, `tests/started/test_lifespan.py` and `tests/wiring/` hold each rule.

## Considered options

- **Compare the sum of the bounds in a request with the budget.** Two ordinary statements, each
  bounded at 6 s, fail it. Each wait in the incidents above was over the budget alone.
- **Compare each bound with the time left in the request.** In a test, the time left is almost
  the whole budget, so this is the same check. In production, it fails a request at random, on
  how long the waits before it took.
- **A deadline on each request, as `asyncio.timeout` around the handler.** A slow request
  becomes a `504`, work in a thread runs on, and a stream needs an exemption. The gate still
  says nothing before the change is merged.
- **An audit hook on `socket.connect`, to find a wait nobody declared.** It sees a socket that
  Python opens, and not one that a driver opens in C: the SQL Server login is one of those. A
  route test uses a double and opens no socket.
- **Postgres through the seam.** Each store call would go through `waiting`. The bounds are set
  for the whole pool, and a route test runs on the memory substrate, so the check would never
  fail.
- **`asyncio.create_task` or `BackgroundTasks` in place of `beside`.** A task copies the context
  of the request that starts it, and FastAPI's `BackgroundTasks` run inside the request, so each
  is still in the request. An unreferenced task can be collected before it ends, and an exception nobody
  retrieves is a line from asyncio at garbage collection.
- **Shared work in a module of the project, named in `per-file-ignores`.** Each such module
  decides again whether its run is in a request, holds its task, retrieves a failure no caller
  awaits, and is cancelled at shutdown. Those are the reasons ruff refuses `create_task`.
- **Shared work outside the request, as `beside` runs it.** A request that joins it waits as long
  as the work, and no `waiting` in it is checked against the budget.
- **A primitive for a loop the lifespan owns.** The order in which loops stop, and what a loop
  that dies does to the process, are the project's. A `TaskGroup` around the lifespan's `yield`
  ends the lifespan when a loop dies, and the server serves on with the database closed.
- **A stream, in the template.** It would have no caller here.

## Consequences

- A wait that nobody wraps in `waiting` is invisible to the gate, and so is a wrapped wait that
  no route test runs. The slow-request alert is the only detector for them, and it fires in
  production. It also fires on waits that each have a bound under the budget and add up past it.
- A wait inside the real adapter is invisible to a route test, which uses a double. The wait goes
  around the call into the port.
- A `def` route cannot enter `waiting`, which is async. A call to a system outside the process
  goes in an `async def` route, in `asyncio.to_thread` inside `waiting`.
- `waiting` does not stop a call in a thread. The driver gets the same bound as its own timeout,
  and nothing checks that the two are equal.
- A `DATABASE_STATEMENT_TIMEOUT` of 9 s or more, or a `DATABASE_ACQUIRE_TIMEOUT` of 10 s or
  more, refuses a web process. A long statement goes in a job, and the worker, which serves no
  request, starts with a longer bound.
- Each caller of shared work gets the result of the work that the first caller started, in the
  context of the first caller: its actor, its request id and its trace. The key names everything
  the result depends on, and nothing checks that it does.
- Two reports name a failure of shared work that a caller awaits: it raises in the caller, and
  `shared work failed` names it. The same holds for work beside the requests.
- `cancel_beside` and `BesideOutlivedItsCancel` also cover shared work, which is in a request.
- Work beside the requests ends with its process, and its result is lost. Work whose loss
  matters is a job.
- On a platform that gives an instance CPU only while a request is open, a task beside the
  requests stalls after the response.
