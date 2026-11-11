# Installation

Two toolchains, one per half.

## Prerequisites

**Node 24+ and pnpm**, for the frontend half:

```bash
brew install node                                     # macOS
corepack enable && corepack prepare pnpm@latest --activate
```

**uv**, for the backend half. It installs the Python the application runs on, so you do not
need a system Python of any particular version:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`make`, `git`, `curl`, `perl` and `python3` complete the set; all five ship with the Xcode command line tools
on macOS and with build-essential and the base system on Debian and Ubuntu. `python3` is not
the application's Python — the one uv installs is. `devtools/dev.sh` and
`devtools/contract-test.sh` use it to put each half in a process group of its own, which is the
only way they can stop what they started. `devtools/gate.sh` uses `perl` to take the lock that
keeps two gates of one project from running at once, because macOS ships no `flock`.
`make install` uses `curl` to fetch a pinned gitleaks, checked against `devtools/gitleaks.sums`,
for the secret scan in the gate. When the release link fails, it fetches the same file through
the GitHub API.

## Install

Commit the project before you install it. `make install` activates the git hooks. The hooks need
a repository.

```bash
git init && git add . && git commit -m "Initial commit"
make install
```

`make install` activates the versioned git hooks, then runs `devtools/install.sh`. The script runs
`pnpm install` in `frontend/` and `uv sync --all-groups` in `backend/` (which installs the Python
version pinned in `backend/.python-version`), and fetches what the gate and the browser tiers
need. Each step downloads only what is absent. Run it again with nothing changed, and it
downloads nothing for the two halves, gitleaks or openapi-typescript. The template's own gate
checks this before each release. pnpm fetches openapi-typescript again once its copy is older
than `dlxCacheMaxAge` in `frontend/pnpm-workspace.yaml`. Nothing checks that Playwright skips a
browser that it already holds.

It also writes `frontend/pnpm-lock.yaml` and `backend/uv.lock`, which do not ship with the
template: a project generated today should resolve against today's registry rather than against
whichever day the template was last touched. **Commit both.** Without them the next clone
resolves its own, and two checkouts of the same commit build different trees — the kind of
difference that surfaces as a bug nobody can reproduce.

To work on one half only:

```bash
pnpm -C frontend install
cd backend && uv sync --all-groups
```

Neither half needs the other's toolchain to install, lint, test or build.

## Verify

```bash
make pre-commit
```

`make pre-commit` runs the commit gate. [development.md](development.md#the-gate) says what it
checks. CI or the release caller runs the full suite.

## Updating from the template

`.copier-answers.yml` records the template this project came from and the exact commit, which
is what lets a later template change reach a project generated months ago:

```bash
uvx --exclude-newer "7 days" copier@9.17.1 update
make install
make openapi
make lint
make schema
make pre-commit
```

Copier resolves against **tags**, so `update` brings you to the template's newest release
rather than whatever is on its default branch. It is a three-way merge against the commit you
generated from, so commit or stash first.

What to expect, because it is not all free:

- **Files you never touched update cleanly** — tooling, gates, `Makefile` targets, docs, ADRs.
- **Some files are yours after the first copy.** `copier.yml` lists them under
  `_skip_if_exists`, and an update never touches them. Each code module holds only your list; the
  module that reads it is the template's, and keeps updating. `docs/frontend.md`,
  `docs/backend.md` and `docs/development.md` are yours too: rewrite them with this project's
  detail. A later template change to one reaches only new projects.
- **Stop receiving the example resource once you have replaced it.** Record the answer before
  you update: set `example_resource: false` in `.copier-answers.yml`, commit, then run the
  update. From then on an update leaves the example resource's own files alone, and does not
  re-add any you deleted. Everything else keeps updating. **Do not give the answer on the update
  itself**, with `--data example_resource=false` or at the prompt: Copier then deletes the
  example's files and `app/jobs.py`, your edits and your own kinds of job included.
  `.copier-answers.yml` says never to edit it by hand. This one edit is expected, and Copier
  keeps the value when it writes the file again.
- **Drop the GitHub workflow if your checks run somewhere else.** Answer once, on the update
  itself: `uvx --exclude-newer "7 days" copier@9.17.1 update --data github_ci=false`. On this
  question the update is meant to delete: it removes the workflow and
  `backend/tests/test_workflow.py`, even a workflow you edited, so commit first. A project
  generated before 0.33.0 keeps its `.github/`: delete it yourself. `docs/development.md` is
  yours, so remove what it says about the workflow too. A new project answers the same question on
  its first copy.
- **Stop receiving the identity stub once you authenticate.** Record the answer the same way:
  set `identity_stub: false` in `.copier-answers.yml`, commit, then update. An update then leaves
  `app/identity/__init__.py` and its tests alone, and still brings `app/identity/shared.py`. Given
  on the update itself, the answer deletes them. A project generated before 0.36.0 has its
  resolver in `app/identity.py`. With `identity_stub: false`, move that file to
  `app/identity/__init__.py` and commit before the update: a folder without `__init__.py` loses
  to a module of the same name. After the update, rename `tenant_for` to `principal_for`, return
  a `Principal`, and import `Unauthenticated` and the other shared names from
  `app.identity.shared`. With `identity_stub: true`, the update deletes `app/identity.py` and any
  edit in it.
- **Files you rewrote come back as conflicts.** The modules under `app/models/` and
  `app/routes/tenant/` are the first any real project replaces. You are porting a pattern rather than accepting a patch.
- **Regenerate the contract and the complexity baseline afterwards.** `openapi.json`,
  `frontend/src/api/schema.ts` and `backend/.complexity-baseline.json` are generated from your
  code, so an update never touches them. `make openapi` rewrites the first two and `make lint`
  the third; if `make lint` reports that complexity rose past the tolerance, the template's
  code is why, and the message says how to record it.
- **Regenerate the schema artifacts too.** `deploy/schema.sql` and `backend/.schema-baseline.json`
  are generated from your `ddl.py`, which by then holds your entries as well as the template's.
  A merged copy of either describes neither project, and `make pre-commit` says so. `make schema`
  rewrites both. Commit them with the update.
- **A uv exclusion table you already have can come in twice.** From 0.35.0
  `backend/pyproject.toml` ships an empty `[tool.uv.exclude-newer-package]`. The merge can keep
  both headers with no conflict, and uv then refuses the file. Keep one header and move your
  entries under it. `backend/tests/test_supply_chain.py` then refuses each entry that is not a
  timestamp inside the cool-off with a floor, and its message says what to do. Your own
  `docs/development.md` keeps the cool-off it states, so correct it by hand.
- **A rule the template adds can refuse code you already have.** From 0.39.0 `make lint` refuses a
  comment in a script as well as in source. From 0.41.0 it refuses a blocking call in an
  `async def`, by ruff's `ASYNC2` rules. `cd backend && python3 devtools/comments.py --strip --tree ..`
  removes the comments the check refuses, and no other text. It stops at the first comment it
  cannot remove without changing what the code does, names it, and leaves that file as it was.
  Remove that one by hand and run it again. Read the diff before you commit it:
  a reason that a comment held belongs in the commit message. `uv run ruff check --select ASYNC2`
  names each blocking call. Move it into a `def` or into `asyncio.to_thread`.
- **From 0.42.0 a write with no actor fails.** Postgres records every write to a table in
  `TENANT_TABLES`, and refuses one when nothing names an actor. A request names its principal's
  actor through `audit_events.serve`: if your identity dependency calls `log.name_principal`,
  call `audit_events.serve` instead. A resolver that writes before it has a principal runs that
  write inside `audit_events.acting_as`. Every worker loop, job and script that writes runs
  inside `audit_events.acting_as(actor)`. An app acting for a person names the person in
  `on_behalf_of`. Until each one does, its first write raises `a write to <table> names no
  actor`, and the loop stops. Your own Postgres tests and fixtures that write rows do the same.
  Store methods no longer take an actor, and record no audit event: delete the recording you
  added for 0.41.0, or each write records two events. `audit_event`, `absent`, `written`,
  `AuditEvent` and `app.store.TASKS` are gone. If you merged `app/store/pg.py`,
  `app/store/migrate.py` or `app/store/ddl.py` by hand, keep `setup=_name_who_acts` on
  `create_pool`, `MIGRATION_ACTING_SQL` in `apply`, and the audit entries. A tenant table with no
  primary key cannot be written: give it one. After the first
  `make migrate` on 0.42.0, an instance of an earlier release cannot write, so replace them all
  in the same release.
  [adr/template/0017](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md)
  says why.
- **From 0.43.0 test files in one worker share their modules and one jsdom window.**
  `frontend/vitest.config.ts` sets `isolate: false`. After each file, `frontend/tests/setup.ts`
  puts back the shared state that tests commonly change. Other state that a file changes now
  reaches the next file in its worker, for example a module-level cache or a listener on `window`.
  The file that changes it puts it back after its last test. If you merged
  `frontend/tests/setup.ts` by hand, take the template's `beforeAll` and the hook after the last
  test as they are: a registration at the top of the module accumulates once for each file. A test
  that fails only after the update can depend on what an earlier file left. Run
  `pnpm exec vitest run --maxWorkers=1 --sequence.shuffle --sequence.seed=<n>` with a few values of
  `<n>`: a failure that comes and goes with the seed depends on the order of the files.
- **From 0.44.0 `make install` and CI install through `devtools/install.sh`.** If your
  `Makefile` or a workflow of yours installs a half its own way, merge it into the script.
- **From 0.44.0 CI refuses a lockfile that does not match its manifest.** Before, `uv sync
  --frozen` did not compare `backend/uv.lock` with `backend/pyproject.toml`. Run
  `cd backend && uv lock` and commit the result before you push the update.
- **From 0.46.0 the event-loop limit is in seconds on the reference core.** On a machine faster
  than a Google Cloud E2 core, a step may use less CPU than before, so a test that passed can
  now fail. The error names the step, and the CPU it uses here and on the reference core.
  Outside the lifespan, do less of that work, or run it in another process. The 0.60.1 step says
  why a thread does not help. `measure_every_step` now takes the limit: if you merged
  `backend/tests/conftest.py` by hand, keep the template's call. A test of yours that holds the
  event loop past the limit holds `step_limit.seconds` and a margin.
- **From 0.47.0 only a test in `tests/started/` or in a tier may start the app.** Any other test
  that enters `with TestClient(create_app())` fails with `StartedOutsideItsFolder`, which names
  it. For a route test, mount its routers with `tests.routed.routed` and the `database` fixture:
  `TestClient(routed(tenant.router, database=database))`. Hand `routed` each other value your
  lifespan puts on `app.state`, by name. Override the `app_state` fixture to hold those values,
  in `tests/routes/conftest.py`: the route guarantee reads it there. With many routes under one
  requirement, mount only the resource router: `app = routed(database=database)`, then
  `app.include_router(tasks.router, dependencies=tenant.router.dependencies)`.
  Move a test of what the lifespan does to `tests/started/`. Move
  `test_a_production_process_on_memory_does_not_start` from your
  `tests/environment/test_environment.py` to `tests/started/test_environment.py`: an update
  brings neither file. In its new file, it calls `monkeypatch.delenv(ENVIRONMENT_ENV)` in place of
  the `unnamed` fixture. In `app/main.py`, put each requirement's router in `ROUTERS` and
  everything around a route in `assemble`, never in `create_app`. `create_server` now takes the
  app it mounts: `create_server(bundle, api)`. `docs/backend.md` is yours, so this release does
  not update it. From the template's copy, take the bullets that start "A route test starts
  nothing", "`create_app` adds nothing but the lifespan" and "A test that starts the app", and
  the `tests/routed.py` and `tests/started/` lines of the layout.
  [adr/template/0018](adr/template/0018-a-route-test-never-starts-the-app.md) says why.
- **From 0.48.0 `routed` builds the app for a set of routers once per process, and shares it.**
  Each test that asks for it gets a new `app.state` and no `dependency_overrides`. A test that
  changes what the app serves fails at its teardown with `SharedAppChanged`: it adds a route to
  the app or to a router the app includes, includes a router, adds a middleware or an exception
  handler, or sets an attribute of the app. Build that app with `tests.routed.assembled`, which
  takes the same arguments and builds a new app on each call. A test that mounts only a resource
  router, as the 0.47.0 step shows, mounts its requirement router with `routed` instead: the
  shared app builds those routes once. A test that asks `routed`
  twice for the same routers fails with `SharedTwiceInOneTest`, and a test that configures
  telemetry fails with `SharedWithTelemetry`. Build each of those apps with `assembled` too.
  Something that reads the state of a shared app after its test ended fails with
  `SharedPastItsTest`: ask for the app in a fixture that each test gets for itself. If you merged
  `backend/tests/conftest.py` by hand, take the template's `pytest_runtest_teardown`, which ends
  each share. Without it, the second test that asks `routed` for an app fails with
  `SharedTwiceInOneTest`. In
  `docs/backend.md`, take the template's bullets that start "A route test starts nothing" and
  "`routed` builds the app".
  [adr/template/0018](adr/template/0018-a-route-test-never-starts-the-app.md) says why.
- **From 0.52.0 a query client from `createQueryClient` must follow a router.** Call
  `followNavigations(router, queryClient)` after you create the client, as `src/main.tsx` does.
  Until then, the client refuses to mount a query over data. If you set defaults on that client,
  merge them: `setDefaultOptions` replaces the default `refetchOnMount`. A client of yours that
  `createQueryClient` does not make reads again on each mount. In `docs/frontend.md`, take the
  template's bullets that start "A read stays current" and "A second `useQuery` call".
  [adr/template/0020](adr/template/0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md)
  says why.
- **From 0.53.0 `make lint` checks the startup count.** An update
  does not bring `backend/.startup-baseline.json`, so `make lint` fails until you record your
  own count. In `backend/`, run
  `uv run python devtools/startup.py --baseline .startup-baseline.json --update-baseline` and
  commit the file. `app/main.py` now imports `app.telemetry` inside `_instrument`, only when
  telemetry is configured. If you merged `assemble` by hand, take the template's.
  [deployment.md](deployment.md#a-cold-start) says why.
- **From 0.54.0 each wait in a request has a bound under the request budget.** Before you take
  the update, check the values your deployments set: a `DATABASE_STATEMENT_TIMEOUT` of 9 s or
  more, or a `DATABASE_ACQUIRE_TIMEOUT` of 10 s or more, now refuses to start. Then:
  - If you merged `assemble` in `app/main.py` by hand, take the template's `waits.instrument(app)`.
    Without it no request is marked, and `waiting` refuses nothing.
  - If you merged `app/lifespan.py` by hand, take `_stop`, which calls `cancel_beside()` before
    `database.close()`.
  - `make lint` now refuses every `asyncio.create_task`, `asyncio.ensure_future` and
    `BackgroundTasks` in the project. Move each before you wrap any call: a `waiting` in a task
    that one of them started raises, because the task is still in its request. Take 0.55.0 with
    this step, and move each to the home that the 0.55.0 step names.
  - Then wrap each call to a system outside the process, except Postgres, in `app.waits.waiting`.
  - Set your slow-request alert's threshold to `REQUEST_BUDGET_SECONDS`. On Cloud Run, run the
    service with CPU always allocated, or work beside the requests stalls after the `202`.
  - `app/waits.py` is one more file in the startup count. Record your count again, with the
    command in the 0.53.0 step.
  - In `docs/backend.md`, take the template's bullets that start "Each wait in a request", "Wrap
    the call into the port", "Start a wait past the budget" and "Postgres is not wrapped".
  [adr/template/0021](adr/template/0021-each-wait-in-a-request-has-a-bound-under-the-request-budget.md) says why.
- **From 0.55.0 `beside` ends with the result of its work, and `SharedWork` runs shared work.**
  Move each `asyncio.create_task`, `asyncio.ensure_future` and `BackgroundTasks` to one of four
  homes:
  - Work that runs past the request, such as a job, a `BackgroundTasks` task, or a build that a
    poll reads later: `app.waits.beside(work, name=...)`, and answer `202`. The task ends with
    the result of `work`. Drop the set that held its tasks.
  - Work that a caller awaits together: an `asyncio.TaskGroup`. Move an `asyncio.gather` without
    `return_exceptions=True` there too. ruff does not refuse it, but it leaves the others running
    after the first failure.
  - Work that several callers await at once, shielded so that one cancelled caller leaves it
    running: `shared: SharedWork[K, T] = SharedWork(name)` from `app.waits`, and
    `await shared.joined(key, start)`. Drop the dict that held the runs, and the
    `asyncio.shield`. Put the tenant, the actor and the arguments in the key. A `waiting` in it
    now has a bound under the budget, also when work outside a request starts it. Shorten a
    longer bound, or move that wait to `beside` and a poll.
  - A loop that the lifespan starts and stops in its own order: one module of yours, named in the
    `per-file-ignores` of `pyproject.toml` as `["TID251"]`, with its reason. The lifespan is not
    in a request, so the loop is not either. The entry also lets the module import `logging` and
    the other banned modules, so keep it to the loops.
  - Do not start a task with `loop.create_task` or `asyncio.Task`. ruff cannot see them, and the
    task is still in its request.
  - In `docs/backend.md`, take the template's bullets that start "Start a wait past the budget",
    "Share work", "The key names everything", "Shared work reports" and "Each task has one of
    four homes".
- **From 0.56.0 each pool is sized from the connection budget.**
  - Set `DATABASE_INSTANCES` in each deployment, or production refuses to start. It is the most
    processes that hold connections at once. Count the revision that drains during a rollout:
    on Cloud Run that is twice the most instances when a rollout overlaps them all.
  - In `app/connection_budget.py`, name each pool or connection that one process opens beside the
    template's pool in `IN_PROCESS`, and each holder that is not one of the processes in
    `OUTSIDE`. Size each of your own pools from its entry there.
  - When one of your own pools gets no connection, raise `DatabaseBusy` from `app.store`, with a
    `retry_after`. The request then answers the same `503`.
  - A loop or a worker that opens a connection meets `ConnectionRefused` and `AcquireTimedOut`
    too. Let it wait and try again. A loop that ends its process on one turns a full server into
    a restart.
  - A request that gets no connection answers `503` with `Retry-After`, and records no request
    event. Every route that verifies a credential declares it, so run `make openapi`.
  - `PostgresDatabase` takes `draws=` and no `max_size=`. A test that wants a pool of one passes
    `Draws(instances=1, in_process={}, outside={}, pool=1)`.
  - `app/connection_budget.py` and `app/store/budget.py` are two more files in the startup count.
    Record your count again, with the command in the 0.53.0 step.
  [adr/template/0022](adr/template/0022-the-connections-of-every-process-fit-inside-max-connections.md) says why.
- **From 0.57.0 each process admits each actor's requests within a bound.** An actor is the
  issuer and the subject that a credential names.
  - Set the numbers in `app/admission_limits.py`. Under the identity stub every request is one
    actor, so the numbers hold for everybody at once, in each process.
  - Keep `in_flight` under the template's pool, which the line `connections budgeted` names.
  - If your resolver reads the database, call `app.admission.admit(request, actor)` where it
    verifies the credential, before that read. `resolved_principal` admits the rest.
  - Make `in_flight` and `burst` each at least the largest read budget in
    `frontend/tests/read-budgets.ts`, plus the streams that screen opens. A draw sends its reads at
    once, and admission refuses rather than queues, so a screen over them gets a `429` on its own
    first draw.
  - A request over a bound answers `429` with `Retry-After`, and records no request event. Every
    route that verifies a credential declares it, so run `make openapi`.
  - A route whose response stays open, such as a stream, calls
    `app.admission.stream(request, principal.actor)` before it answers.
  - A route test that sends more requests at once than `LIMITS` admits gets a `429`.
  - `app/admission.py` and `app/admission_limits.py` are two more files in the startup count.
    Record your count again, with the command in the 0.53.0 step.
  [adr/template/0023](adr/template/0023-the-server-admits-each-actor-s-requests-within-a-bound.md) says why.
- **From 0.58.0 a read has an outside cause.** `pnpm conformance` refuses `invalidateQueries`,
  `refetchQueries` and `resetQueries` outside `src/query-client.ts`, `invalidateForServerEvent`
  outside `src/streams/`, and `mutate` or `mutateAsync` in a `useEffect`. It names each call.
  - A mutation that invalidates names the keys in `meta: { invalidates: [key] }`, and drops the
    call from its `onSuccess` or `onSettled`. The mutation cache invalidates them when it
    settles, and the mutation stays pending until the reads end, as an awaited call did.
  - A stream that invalidates moves under `src/streams/`, and calls
    `invalidateForServerEvent(queryClient, [key])`.
    Events merge until a read can start. Handle the returned promise's rejection at the stream boundary.
    Pass the query function's `signal` to each API read for cancellation and the event read budget.
    [adr/template/0030](adr/template/0030-server-events-merge-and-share-a-read-budget.md) defines this contract.
  - A mutation that an effect starts moves to the person's action, or to a listener the effect
    registers for that action. A mutation that starts after a read answered permits no read, so
    its invalidation stops with `CycleStopped`. That includes a write awaited after another write,
    and a write in a route's loader after it reads: send the writes in one `mutationFn`, or
    start them with no `await` between.
  - An effect that calls `.click()`, `requestSubmit()` or `dispatchEvent()` fails
    `pnpm conformance`. Do the work the input would start, from a real outside cause.
  - Any other invalidation reads in answer to the client's own cache. Remove it. If a screen
    then shows old data, find the outside cause it misses: a mutation, a server event or a
    backstop.
  - Run `pnpm -C frontend exec vitest run tests/first-draw.test.tsx tests/components`. A read
    with no outside cause fails with `CycleStopped`, which names the key.
  - `ApiError` takes a fourth argument, `retryAfterSeconds`. Pass `null` where you build one.
  - A query retries only `Unreached`: a request that never reached the server. A test that waited
    for a retry of a `5xx` now sees the error at once.
  - A `429` or `503` with `Retry-After` holds every request to the API until the wait ends. Each
    one fails with `Held`, and sends nothing. Any other refusal holds nothing.
  - If you merged `frontend/src/query-client.ts` or `frontend/tests/render.tsx` by hand, take the
    template's copy. `renderWithQuery` now builds the app's client.
  [adr/template/0024](adr/template/0024-every-read-has-an-outside-cause.md) says why.
- **From 0.59.0 a tab reloads into the deployed build at the next navigation.** Each build
  writes `frontend/dist/build.json`. Then:
  - Serve `build.json` with `cache-control: no-cache` from every host that serves the bundle.
    `app.serve` does. A host that caches it hides each new build from each open tab.
  - If you merged `src/main.tsx` or `vite.config.ts` by hand, take the template's
    `followTheDeployedBuild` call and its `name-the-build` plugin.
  - A client event can now have the kind `build`. If you filter client events by kind, add it.
  - In `docs/frontend.md`, take the template's line for `src/build.ts` and the bullet that starts
    "A tab runs the deployed build".
  [adr/template/0025](adr/template/0025-a-tab-learns-of-a-new-build-and-reloads-at-the-next-navigation.md)
  says why.
- **From 0.60.0 a deployment can replace each number of admission, and evaluate it.**
  - `ADMISSION_IN_FLIGHT`, `ADMISSION_PER_SECOND`, `ADMISSION_BURST` and `ADMISSION_STREAMS` each
    replace one field of `LIMITS`. With `ADMISSION_MODE=evaluate` the process admits a request
    over a bound and writes `admission would refuse`. The process refuses to start on a value
    that is not one.
  - A `429`'s `detail` now names the bound that refused it. A test that compared the whole
    sentence must compare its start instead.
  - `devtools/contract-test.sh` reads `devtools/contract-test.env`, which is yours, for the
    admission numbers of its backend. If your suite gets a `429`, run
    `ADMISSION_MODE=evaluate make test-contract`, read the count by bound at its end, and set
    `ADMISSION_BURST`, `ADMISSION_PER_SECOND` or `ADMISSION_IN_FLIGHT` in that file. Drop any
    switch you added to give the suite unbounded admission, and any edit you made to
    `contract-test.sh` for it.
  [adr/template/0023](adr/template/0023-the-server-admits-each-actor-s-requests-within-a-bound.md)
  says why.
- **From 0.60.1 the rules no longer send CPU work in a request to a thread.** A thread frees
  the event loop only for code that releases the interpreter lock. A thread also hides CPU work
  from the event-loop limit and from `event loop held`. In `docs/backend.md`, take the
  template's bullet that starts "A thread frees the event loop only", and the bullet that
  starts "A lifespan step holds the event loop". The update needs nothing else. To find a hold
  you hid this way, look for CPU work in a request that you moved into `asyncio.to_thread` or a
  `def` route. Do less of it, or run it in another process.
- **From 0.61.0 the pool times each wait for a connection.** `db.client.connection.wait_time`
  is a histogram in seconds. It holds the waits that ended in a connection or in
  `AcquireTimedOut`. `Pooled`
  in `app/store/__init__.py` now has `time_waits`. If a substrate of yours counts its
  connections, give it `time_waits`. Without it, the app refuses to start with telemetry on,
  and the error names `time_waits`.
- **From 0.62.0 every store read is a page.** `GET /tasks` answers `{tasks, next}`, newest
  first, and takes `after`, the id of the last task on the page before. A cursor that names no
  task answers `404`. `TaskStore.list` takes `after` and answers a page of at most `PAGE_MAX`
  rows, or `None`. `AuditEvents.list` takes `after` and answers a page, or raises
  `NoSuchAuditEvent`. `PAGE_MAX` is in `app.models`. Then:
  - Give each list of your own stores the same shape, and run your store contract suite.
  - Where your code read a whole list, read the page it answers. A test that needs the whole list
    reads it with `every_task` or `every_event` from `tests/pages.py`.
  - If you merged `src/components/task-list.tsx` or `src/mocks/` by hand, take the template's
    `useInfiniteQuery` and the paged mock store.
  - `tasksApi.list` takes `after`. Code that reads the `["tasks"]` cache holds
    `InfiniteData<TaskPage>`, not `Task[]`. A `page.route` in an e2e spec that answers
    `/api/tasks` answers `{tasks, next}`.
  - Run `make openapi`, and commit `openapi.json` and `frontend/src/api/schema.ts`.
  - In `docs/backend.md`, take the template's bullet that starts "A store read answers one page".
    In `docs/frontend.md`, take the bullet that starts "A list is read a page at a time".
  [adr/template/0026](adr/template/0026-every-store-read-is-a-page.md) says why.
- **From 0.63.0 work that outlives its request is a job that a worker process claims.** Before
  you take the update, list each `beside` your project starts, with its bound.
  - Run `make migrate`. It adds `job_claims` and `jobs`. `job_claims` is the second table that no
    policy scopes: it holds what a worker reads to claim the next job of any tenant, and no
    input and no outcome.
  - `beside` takes `within=`, under `REQUEST_BUDGET_SECONDS`. Move each `beside` whose work may
    take longer to a job: name its kind in `app/jobs.py`, with a handler that takes the store of
    the job's tenant and the job's input and returns the outcome. The route asks for it with
    `Database.jobs(tenant_id).ask(kind, input)`, through `JobsDep`, and answers `202` with the
    job. A screen reads `GET /jobs/{id}` with a `refetchInterval` until the job is not
    `pending`. A handler runs at least once, so make it idempotent. An input that `jsonb` cannot
    hold raises `UnstorableJson`: answer it with `UnstorableInput`, from `app.refusal`.
  - Give each remaining `beside` a `within=` under the budget.
  - ruff now refuses `asyncio.sleep`, `anyio.sleep` and `time.sleep`, in each module that
    `per-file-ignores` does not exempt from `TID251`. Wait through
    `app/pacing.py`: `every` for a loop on a period, `backoff` before a retry, `yield_now` for a
    turn of the loop, `poll_outside` for a system outside the process with no push. In a test,
    use `tests/clock.py`. A loop that sleeps to read rows your own system writes again waits on
    a wake: send `pg_notify` in the transaction that writes, and `LISTEN` for it.
  - Run `python -m app.worker` beside the web process, as a service of its own with the same
    environment. `docs/deployment.md` says how. Count its processes in `DATABASE_INSTANCES`. The
    development loop on Postgres is two processes, so an unset `DATABASE_INSTANCES` there is two.
  - `MemoryDatabase` and `PostgresDatabase` take `kinds=`, the map of `app/jobs.py`. A test that
    builds one passes it. A route test on memory waits for each job with
    `MemoryDatabase.drained()`.
  - Each process now holds `TEMPLATE_IN_PROCESS` beside its pool: the worker's `LISTEN`
    connection. The pool of each process is one connection smaller.
  - If you merged `devtools/dev.sh` or `devtools/contract-test.sh` by hand, take the template's
    worker. Run `make openapi`, and commit `openapi.json` and `frontend/src/api/schema.ts`.
  - In `docs/backend.md`, take the template's bullets that start "Make a wait past the budget a
    job", "Start short work the caller does not wait for" and "Each task has one of five homes".
    In `docs/development.md`, take the bullet that starts "`make dev` with a `DATABASE_URL`".
  [adr/template/0027](adr/template/0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md) says why.
- **From 0.64.0 a read stays fresh while it is current, and the read guard counts each cause.**
  - The default `staleTime` is infinite while a read is current, and 0 after: until the next
    navigation, focus or reconnect, unless this tab invalidates the key. `fetchQuery`,
    `ensureQueryData` and `prefetchQuery` then answer from the cache, and a part with
    `refetchOnMount: true` reads nothing. A read after a person's input invalidates the key
    through a mutation, or calls `refetch()`.
  - A query with its own `staleTime` replaces the default. Combine it with `readIsCurrent` from
    `src/query-client.ts`, or a query that turns `enabled` over current data can be stopped.
  - A `focusout` is no longer a person's input. A save on blur is still the person's mutation,
    because the person pressed a key or a pointer before leaving the field.
  - If you patched `admit` in `src/query-client.ts` to keep the time of an earlier read, take the
    template's copy. A test that polls runs TanStack's timer: with the real clock, or with `Date`
    and the timers faked and advanced together.
  [adr/template/0024](adr/template/0024-every-read-has-an-outside-cause.md) says why.
- **From 0.65.0 a job's handler takes the job's tenant, and `PAGE_MAX` is in `app/models/shared.py`.**
  - `app/jobs.py` exports `kinds(database)` in place of `KINDS`. It returns the map of each kind
    to its handler. A handler takes the job's tenant and the job's input. Take each store in
    `kinds`, for that tenant, and hand it to the code that does the work:
    `lambda tenant_id, input: close_a_task(database.store(tenant_id), input)`. The update does
    not change `app/jobs.py`, so make the change by hand.
  - `MemoryDatabase` and `PostgresDatabase` take `kinds=kinds`, the function, and call it with
    themselves. `PostgresDatabase.kinds` holds the map, and `work` and `run` in `app/worker.py`
    read it there: drop the map from each call. `store_of_the_job` is gone, and `attempted`
    takes the tenant in place of a store.
  - `app/models/tasks.py` imports `PAGE_MAX` from `app.models.shared`. In
    `tests/models/layers.py`, give `tasks` the layer `shared`, or `tests/models/test_layering.py` names the import.
  - In `docs/backend.md`, take the template's bullet that starts "Make a wait past the budget a
    job".
  - With `example_resource: false`, an update neither writes nor changes `app/jobs.py`, as for
    the example's own files. Its first render imports `app.task_jobs`. If your `app/jobs.py`
    imports it, or you have no `app/jobs.py`, write one with
    `def kinds(database: Database) -> Kinds: return {}`, with `Database` from `app.store` and
    `Kinds` from `app.store.jobs`. Also take `PAGE_MAX` into `app/models/__init__.py` from
    `app.models.shared`, and import it from `app.models.shared` in your own `app/models/tasks.py`
    if you kept one. In your own `tests/routes/test_tenant_scoping.py`, take three things from the
    template's copy: `kinds` in `app/jobs.py` in `SCOPING_FUNCTIONS`, a `Site` named by its path
    under `app/`, and `test_a_job_obtains_a_store_only_for_the_tenant_of_the_job`.
  [adr/template/0027](adr/template/0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md) says why.
- **From 0.67.0 the reads that start in one task are one request.**
  - Give each GET route that reads only the database `openapi_extra=PART`, from `app.reads`, and
    run `make openapi`. It writes `frontend/src/api/parts.ts`. Leave a route that waits on
    another system alone: `waiting` raises `PartWaitsOutside` in a part.
  - Each budget in `tests/read-budgets.ts` is now exact, and a budget above 1 names `because`.
    Run `pnpm -C frontend exec vitest run tests/first-draw.test.tsx`, and set each budget to the
    requests it reports. The test now waits for each render, so a screen with a slow render can
    count more reads than before.
  - `app/read_limits.py` is new, and yours. In `tests/models/layers.py`, give the new module
    `app/models/reads.py` the entry `"reads": frozenset()`, or `tests/models/test_layering.py` names it.
  - The contract suite sends each part of a group as a request of the rate. If
    `make test-contract` gets a `429`, run `ADMISSION_MODE=evaluate make test-contract` and raise
    `ADMISSION_BURST` in `devtools/contract-test.env`. The template's own suite needs 60.
  - In `docs/frontend.md`, take the template's bullet that starts "A screen's first draw is one
    request".
  - If you keep your own `tests/routes/test_guarantee.py`, add `("GET", "/reads")` to
    `PUBLIC_ROUTES`. Each part presents the credential of its group.
  - The process now refuses to start when `READS_MAX_PARTS`, 16, is more than `burst`, or when
    `READS_PART_CONCURRENCY`, 4, is more than `in_flight`. This includes a test that lowers
    `ADMISSION_IN_FLIGHT`. Raise the admission numbers in `app/admission_limits.py`, or lower
    the numbers in `app/read_limits.py`. `in_flight` no longer needs to hold a whole draw.
    Measure with `ADMISSION_MODE=evaluate` before you lower it.
  [adr/template/0029](adr/template/0029-the-reads-that-start-in-one-task-are-one-request.md) says why.
- **A custom CI must call `make suite` before promotion.** Take the new `gate-commit` and `suite`
  targets together. Keep the frontend Node, smoke and DOM selections from `vitest.config.ts`.
  `make pre-commit` now checks only the commit gate.
- **From 0.70.0 a loop outlasts a database outage, and `DatabaseBusy` is `DatabaseUnavailable`.**
  - Import and raise `DatabaseUnavailable` from `app.store` where you used `DatabaseBusy`.
  - The line `database busy` is now `database unavailable`, and it also names `cause`. Change
    each alert and each query on it.
  - A request whose connection ends answers `503` with `Retry-After`, not `500`. A transaction
    left idle past its bound now raises `ConnectionLost`, where it raised asyncpg's
    `InterfaceError`.
  - Run each loop that runs until its process stops through
    `app.loops.rounds(name, each_round)`, with one round as a function. Delete each catch of every
    error in it, and its `BLE001` entry in `per-file-ignores`. `tests/loops/test_every_loop_runs_through_rounds.py` names each `while True` with no `break` and no `return`.
  - `log.database_busy(refusal)` is now `log.database_unavailable(refusal, cause)`.
  - Inside `PostgresDatabase.connection()` and `scoped()`, an error of asyncpg or of the socket
    after which the connection ended raises `ConnectionLost`. A catch of an asyncpg error there
    that also expects a connection that ended catches `ConnectionLost` instead.
  - The worker no longer stops when its `LISTEN` connection ends, and `WakesLost` is gone.
    `JobClaims.listening`, a block, is now `JobClaims.listen`, which runs until its connection
    ends. `PostgresDatabase.dedicated` raises `ConnectionRefused` where a new connection does not
    open.
  - A step of an attempt that the database does not serve runs again until the lease would end.
    Watch the new line `attempt abandoned`. `Claim` has a new field, `held_until`, and
    `JobClaims.renew` returns the new one.
  [adr/template/0027](adr/template/0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md) says why.

- **From 0.71.0 server events merge and share a client read budget.**
  - Keep event mapping in `src/streams/`. Continue to call `invalidateForServerEvent(client, keys)`.
  - Forward each query function's `signal` to every API read it starts. The signal carries
    cancellation and the event budget. Calls without it remain outside that budget.
  - Handle the returned invalidation promise's rejection. Do not block stream delivery while
    waiting for refresh completion. A terminal refresh failure or cancellation now rejects that promise.
    A canceled event cause can resume once on a later read. Another cancellation needs a new cause.
  - Events during an active read require a trailing read. Events during the trailing read
    remain pending. Repeated events no longer grant separate reads.
  - Configure event limits with the second argument to `createQueryClient` when measurements
    require it. Count admitted parts, retries, pages, and fallbacks alongside ordinary traffic.
    A client budget cannot enforce the actor limit across clients and tabs.
  - Stop `followNavigations` and clear the old query client when a session ends.
  [adr/template/0030](adr/template/0030-server-events-merge-and-share-a-read-budget.md) states the contract.

- **From 0.73.0 every column has a byte bound, and every page has a byte bound.**
  - `make migrate` adds a `CHECK` on the bytes of each column of the template's tables, then
    validates it against the rows already stored. A stored value over its bound stops the
    migration, and the error names each bound such a value breaks. Measure the largest values
    in a copy of production before you deploy. Shorten each one over its bound, or move it out of
    the row, and migrate again.
  - `POST /tasks` refuses a title of more than `TITLE_MAX`, 500 characters, with a `422`. A job
    input or outcome over `JSON_BYTES`, 256 KiB as JSON, is `UnstorableJson`.
  - Give each column of variable width in your own tables a bound: add it to `COLUMN_BYTES` in
    `app/store/ddl.py`. `tests/integration/test_bounds_on_postgres.py` names each column with
    none. A column whose stored values are over the bound it should have goes in
    `UNBOUNDED_COLUMNS` in that test, with the reason, until its values move out of the row.
  - Read each list of yours with `fetch_page` from `app/store/pages.py`.
    `tests/store/test_pages.py` names each other read of many rows. `TaskStore` and
    `AuditEvents` already use it.
  - `request completed` has a new field, `pages_read`. Alert on a route whose count grows.
  [adr/template/0026](adr/template/0026-every-store-read-is-a-page.md) and
  [adr/template/0031](adr/template/0031-the-database-bounds-the-bytes-of-every-value.md) say why.

- **From 0.74.0 every successful answer has a largest size, and each first draw holds one row a
  page.**
  - `tests/models/test_response_bounds.py` names each route whose answer has no largest size,
    with the path of the part that has no bound. Bound each part in its model: `max_length` on a
    `str` or a `list`, and `MAX_BYTES` from `app.models` on a value that is any JSON. A route
    whose schema cannot bound it goes in `BOUNDED_ELSEWHERE` in that test, with the code that
    bounds it instead.
  - `Task`, `TaskPage` and `Job` bound their ids, names and titles. `Job.outcome` gives
    `MAX_BYTES`. `app/models/jobs.py` now imports `app.models.shared`, so in
    `tests/models/layers.py`, which is yours, give `jobs` the layer `{"shared"}`.
  - `readsOf` draws each screen at one row a page. In each mock of a list of yours, read the
    page size from `pageRows()` in `src/mocks/store.ts`, not `PAGE_MAX`.
    `tests/api/pages.test.ts` names a mock that does not.
  - A part of `GET /reads` no longer carries `set-cookie`, and a group counts its parts in UTF-8
    bytes. If a part renewed a session cookie, renew it on a request that is not a part.
  [adr/template/0028](adr/template/0028-every-finite-response-has-a-byte-bound.md),
  [adr/template/0019](adr/template/0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md)
  and [adr/template/0029](adr/template/0029-the-reads-that-start-in-one-task-are-one-request.md)
  say why.

**If you deleted an entry from `ddl.py`, read this before updating.** Deleting one used to be
silent. It is not any more: every database that applied that entry reports a key your build no
longer carries, so `check` refuses to serve it and `make schema` refuses to forget it. Both name
the key. Either put the entry back, or, if you are certain no database ever ran it, delete its
row from `applied_once` and its line from `backend/.schema-baseline.json` in the same commit.
Deleting the demo `tasks` entries after migrating a database with them is the usual way into
this. [docs/schema.md](schema.md) says what the baseline is and why it refuses.

If you never intend to take template updates, delete `.copier-answers.yml` and the question
stops arising. That is a legitimate choice; making it deliberately is the point.
