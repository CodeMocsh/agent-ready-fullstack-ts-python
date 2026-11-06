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
for the secret scan in the gate.

## Install

```bash
make install
```

That activates the versioned git hooks, then runs `devtools/install.sh`. The script runs
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
make lint test
```

The last step starts both halves and runs the contract suite against the real backend, so a
green run means the two agree.

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
  example's files, your edits included. The file says never to edit it by hand. This one edit
  is expected, and Copier keeps the value when it writes the file again.
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
  - Run `make test`. A read with no outside cause fails `tests/first-draw.test.tsx` or a component
    test with `CycleStopped`, which names the key.
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
- **Re-run the gate.** `make pre-commit` is the check that the merge left something coherent.

**If you deleted an entry from `ddl.py`, read this before updating.** Deleting one used to be
silent. It is not any more: every database that applied that entry reports a key your build no
longer carries, so `check` refuses to serve it and `make schema` refuses to forget it. Both name
the key. Either put the entry back, or, if you are certain no database ever ran it, delete its
row from `applied_once` and its line from `backend/.schema-baseline.json` in the same commit.
Deleting the demo `tasks` entries after migrating a database with them is the usual way into
this. [docs/schema.md](schema.md) says what the baseline is and why it refuses.

If you never intend to take template updates, delete `.copier-answers.yml` and the question
stops arising. That is a legitimate choice; making it deliberately is the point.
