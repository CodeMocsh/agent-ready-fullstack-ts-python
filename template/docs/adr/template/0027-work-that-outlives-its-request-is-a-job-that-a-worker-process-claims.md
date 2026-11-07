# 0027. Work that outlives its request is a job that a worker process claims

Date: 2026-11-06

## Status

Proposed. It replaces the rule of
[0021](0021-each-wait-in-a-request-has-a-bound-under-the-request-budget.md) that sends work past
the request budget to `beside`. It amends
[0022](0022-the-connections-of-every-process-fit-inside-max-connections.md), and it makes one
exemption to [0002](0002-tenant-isolation-is-forced-and-always-on.md).

## Context

0021 sends a wait past the request budget to `beside`. `beside` starts a task in the memory of
the process that served the request. 0021 states the cost: the work ends with its process, and
its result is lost. It leaves the record of the work to the project.

A project generated from this template measured the cost in production, from 2026-11-05 to
2026-11-06, on instances of one vCPU:

- 2,162 s of event-loop holds had no request in flight, and 121 s had one. The holders were the
  project's long work: parses, a recursive scrub of JSON, and the merge of what a reading wrote.
- A cheap read with no hold near it answered in 0.12 s at p99. A cheap read that overlapped a
  hold answered in 0.42 s at p50 and in 6.3 s at p99. 24% took more than 1 s.
- The CPU of that work made the platform start instances for load, and the load alert fired.
- `asyncio.to_thread` did not help, because the parser holds the GIL.

The same project used `beside` for six kinds of work. Their bounds were 120 s, 300 s, 1,800 s
and 3,600 s, a wait of about 48 s on a sandbox, and image builds of several minutes. No bound
was between 10 s and 120 s.

An audit of the project found three kinds of coupling to the memory of one process. Each works
in one process, and each fails when a second process does the work:

- A long job in `beside`. An instance swap ends it, and the instance that serves pages pays its
  CPU.
- A wait that reads the project's own rows again and again, every 0.2 s, 1 s or 5 s, for a write
  that another process makes.
- A result that a request reads from the memory of the process that made it.

More CPU for the same process does not help. One event loop uses one core.

## Decision

- **A job is one unit of durable work.** It is a row in Postgres, and a worker process claims
  it. A request that asks for work past the request budget writes the job in its own
  transaction, answers `202` with the id of the job, and does nothing more. `app/jobs/` holds
  the mechanism. The project's `app/jobs.py` maps each job kind to its handler, as
  [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) says. The
  template ships that map with the one kind of the example resource.
- **Two tables hold a job.** `job_claims` holds only what a claim and a sweep read: the id, the
  tenant, the kind, the state, the lease token, the end of the lease, the attempts, and the time
  the job is ready. `jobs` is a tenant table. It holds the input, the actor that asked, and the
  outcome. `jobs` references `job_claims` on `(tenant_id, id)`, so a claim cannot name the job
  of another tenant.
- **`job_claims` is the second exemption from isolation.** A worker claims the next job of any
  tenant, so the claim cannot be scoped to a tenant before it is made. `EXEMPT_FROM_ISOLATION` in
  `tests/store/test_schema.py` names it, and a test asserts that it has only the columns above.
  The application role can read every tenant id and every job kind. It cannot read an input or
  an outcome. `job_claims` has no audit trigger, because it is not a tenant table. The writes
  of a job to `jobs` and to every other tenant table are audited.
- **A claim takes one row with `FOR UPDATE SKIP LOCKED`, and sets a new lease token.** It takes a
  job that is ready, or a job whose lease has ended. So a job that a dead worker held runs
  again. Each claim counts one attempt. The worker renews the lease while the handler runs.
- **Every transaction of a job holds its claim until it commits.** The first statement of each
  transaction is a `SELECT ... FOR SHARE` on the row in `job_claims`, with the token of this
  attempt and a lease that has not ended. When no row matches, the transaction raises
  `LeaseLost` and rolls back. While the share lock is held, no claim can take the row, so a check
  at the start of the transaction is enough. This is the fence. A worker that was slow writes
  nothing after another worker claims the job.
- **A handler gets a fenced store, and no `Database`.** The mechanism opens the store with the
  tenant of the claim, runs the handler in `acting_as` the actor in `jobs`, and puts the fence in
  each transaction of that store. So
  [0017](0017-the-database-records-every-write-and-the-request-layer-every-request.md) records
  each write of a job under the actor that asked for it. A handler cannot open a transaction
  that the fence does not check.
- **The end of a job is a fenced write.** The transaction that writes the outcome to `jobs` also
  marks the claim done. A worker whose lease ended cannot end the job.
- **A job runs at least once.** A handler is idempotent. A test runs each kind in `app/jobs.py`
  twice on one input, and asserts one outcome.
- **A job that fails `MAX_ATTEMPTS` times is dead.** The line `job dead` names it, and its
  outcome says why. A job that fails fewer times is ready again after a delay that grows with
  each attempt.
- **A wait on the work of another process is a wake, never a loop of sleeps.** The write of a
  job sends `pg_notify` in its own transaction, so the wake comes only after the commit. The
  worker hears it through `LISTEN`. A sweep on a long period is the backstop, and it also finds
  leases that ended. This is
  [0024](0024-every-read-has-an-outside-cause.md) on the backend: each read has an outside
  cause.
- **`GET /jobs/{id}` answers the state and the outcome of a job.** The browser learns that a job
  ended by reading it again. A screen that shows a job reads it with a `refetchInterval`
  backstop while the job is queued or running. The template ships no stream. A project with a
  stream sends a server event when a job ends.
- **The web process and the worker process have one entry point each.** `app.main:app` serves
  requests. `python -m app.worker` claims jobs and serves no request. No variable chooses a
  role. The web process starts no job loop, and a test asserts that a started app runs none.
- **Every development loop on Postgres runs two processes.** `make dev` with a `DATABASE_URL`
  starts both. The gate starts a worker beside the live backend, in the run through the dev
  proxy and in the run through `app.serve`. A contract test asks for a job through the API, and
  reads its outcome through the API.
- **The memory substrate runs its own jobs.** `MemoryDatabase` holds its rows in the memory of
  one process, so no other process can claim them. It runs each job after the write that asks
  for it, in that process, through the handler in `app/jobs.py`. A route test waits for each job
  with `await database.jobs.drained()`. No variable chooses this. The substrate does.
- **`JobsContract` in `tests/jobs_contract.py` holds the contract once.** Each binding runs it,
  as `TaskStoreContract` does for the stores. On Postgres it also runs the kill, the reclaim and
  the fence: a slow attempt's write to a tenant table fails with `LeaseLost` after another
  worker claims the job.
- **`DATABASE_INSTANCES` counts the worker processes too.** `IN_PROCESS` in
  `app/connection_budget.py` is the most that one process holds beside the template's pool. The
  worker's `LISTEN` connection is in it. In the development loop on Postgres, an unset
  `DATABASE_INSTANCES` is two.
- **The worker has its own `Timeouts`.** The rule of 0021 that refuses a statement bound of 9 s
  or more is a rule of the web process. The lease is longer than the period of its renewal plus
  the worker's statement bound, because a renewal waits for each transaction that holds the
  claim.
- **`beside(work, name=..., within=...)` takes a bound under `REQUEST_BUDGET_SECONDS`.** `beside`
  is for work that the caller does not wait for, not for work that is long. Work that may take
  longer is a job. A bound past the budget raises, and its message names jobs. The message of
  `WaitOutlastsTheRequest` names jobs too.
- **ruff refuses a bare sleep.** `asyncio.sleep`, `anyio.sleep` and `time.sleep` are in
  `banned-api`, and each message names the helpers in `app/pacing.py`. Each helper names why it
  waits:
  - `every(period)` for a periodic loop;
  - `backoff(...)` for a retry inside one call, with a bound;
  - `yield_now()` to give the event loop a turn;
  - `poll_outside(check, every=..., within=...)` for a system outside the process that has no
    push, with a deadline.
  No helper waits on rows that this system writes. That wait is a wake.
- **`check_template.sh` plants a `beside` with no bound and a loop of sleeps**, and expects the
  lint to fail on each.

## Considered options

- **Keep long work in `beside`, and let the project record it.** That is 0021 today. The project
  that measured the cost rebuilt the record for each kind of work, and still lost the work at
  each swap of an instance.
- **A library as the Postgres adapter: `procrastinate` or `pgqueuer`.** Neither has a fence for
  each claim. `procrastinate` 3.10.0 ends a job by its id and its state, and it names the holder
  by worker, not by claim. `pgqueuer` 1.6.0 deletes a finished job by its id, with no check of
  the holder. In both, a slow worker ends a job that another worker took. Neither owns the
  transactions of the handler, so neither can check a fence in them. Each keeps the input in the
  table that a claim reads. Each brings a schema and migrations of its own, outside `ddl.py` and
  the release step. `procrastinate` needs psycopg, a second driver beside asyncpg. With our own
  lease beside either, two records name the holder of a job.
- **A fence checked in each statement, or at the end of the job.** A claim can commit between the
  check and the commit of the write. The share lock closes that gap.
- **One process with a role that runs both, for the development loop and the tests.** Nothing
  crosses a process there. Each coupling that the audit found passes every test and fails in
  production.
- **Two processes in one container.** They share the vCPU, so the contention stays. The platform
  scales on CPU, so the work of a job starts instances for requests. Each instance brings its
  own worker, and the budget of 0022 grows with them.
- **A queue outside Postgres**, such as Cloud Tasks, Redis or SQS. The job is then not written in
  the transaction that asks for it. A crash between the two loses the job, or runs it for a
  write that rolled back. It is one more system, and one more credential.
- **A role with `BYPASSRLS` for the worker, or a `SECURITY DEFINER` claim function.** Each one
  undoes a test of 0002 that asserts its absence. The exemption of one table with no input and
  no outcome is smaller.
- **A claim that loops over the tenants, scoped to each.** The list of tenants is itself a read
  across tenants, and the worker waits on the slowest tenant.
- **On the memory substrate, a job that stays queued, and a start line that says so.** A job
  that never runs looks like a job that is slow. A start line is a warning where the code
  cannot correctly proceed.
- **On the memory substrate, refuse to write a job.** `make test` and `make dev` with no
  `DATABASE_URL` then cannot run a route that asks for a job.
- **A ceiling for `beside` above the request budget.** No measured use of `beside` had a bound
  between 10 s and 120 s. Shutdown spends the 10 s after `SIGTERM` on `CANCELLED_WITHIN_SECONDS`
  and `CLOSED_WITHIN_SECONDS`, so work beside the requests past that is cancelled at each swap.
- **`to_thread` for the CPU of a job.** A parser that holds the GIL holds the loop still.

## Consequences

- A deployment runs one more process that is always on. It claims jobs while nobody has the app
  open. The platform's CPU allocation that is always on moves from the web process to the
  worker.
- When the worker is down, jobs wait. A deployment needs an alert on the age of the oldest job
  that is ready.
- The CPU of a job moves to the worker. It does not get smaller. A held loop in the worker delays
  the renewal of each lease it holds, and a hold past the lease ends the attempt with
  `LeaseLost`. So `loop_watch` runs in the worker, and the lease is longer than its worst hold.
- The application role reads every tenant id and every job kind in `job_claims`.
- The web process reserves the worker's `LISTEN` connection in its share, and does not use it.
- A job's input is a row in `jobs`. A large input is read in pages, as
  [0026](0026-every-store-read-is-a-page.md) says.
- On the memory substrate one process serves requests and runs jobs. Its development loop does
  not show a coupling to the memory of one process. The loop on Postgres does.
- Nothing checks that a `poll_outside` waits on a system outside the process. A review does.
- A project that runs long work in `beside` moves each kind to `app/jobs.py`. The upgrade step in
  `docs/installation.md` lists the moves, and the bound refuses each one that is left.
