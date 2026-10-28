# 0018. A route test never starts the app

Date: 2026-10-27

## Status

Accepted.

## Context

The lifespan of `create_app` is the process's startup. It verifies the substrate, builds every
route before the first request, says what the deployment authenticates, and runs whatever the
project adds there. A project adds to it as it grows: a compiled pipeline, a validated spec, a
client for each connector.

A route test asserts the HTTP contract, and needs none of that. The obvious fixture is
`with TestClient(create_app())`, one per test, and it runs the whole lifespan in every route test.
In the template, one start costs 0.0034 s of CPU. In a project generated from it, with 144 routes
and a pipeline built at startup, one start cost 0.69 s of CPU. 1,046 tests started the app, and
those starts used 794 of the suite's 1,070 s of CPU. 7 of the 1,046 tests used what the
lifespan built. Nothing failed while the cost grew, so nothing said so.

This is Meszaros's *General Fixture*, which gives *Slow Tests*: a fixture built for every test,
holding more than any one test uses. The cure is to shrink the fixture. FastAPI runs the
lifespan only inside `with TestClient`, so an app that is never started still serves a request.

## Decision

- **Three layers.** A unit test calls a function. A **route test** mounts the routers under
  test with `tests.routed.routed`, and starts nothing. A test of the **started app** runs the
  lifespan of `create_app`, and lives in `tests/started/` or in a tier.
- **One assembly.** `assemble` in `app/main.py` puts everything around a route: the refusal of an
  unresolved tenant, the request event, the request line and the telemetry. `create_app` is
  `assemble(*ROUTERS, lifespan=lifespan)`. `routed` is `assemble(*routers, lifespan=None)`, with
  `database` and each seam a test names on `app.state`. So a route test gets the answers the app
  gives.
- **The guard.** `the_app_starts_only_where_it_may` in `tests/conftest.py` replaces the lifespan
  of `create_app` outside `tests/started/` and the tier folders. A test there that starts the app
  fails with `StartedOutsideItsFolder`, which names the test and both places it can go. Building
  the app is not starting it. `tests/test_started.py` holds the guard to that.
- **A fresh substrate per test.** The `database` fixture is a new `MemoryDatabase` for each
  test. `tests/integration/conftest.py` puts Postgres under the same name.
- **The lifespan stays cheap.** Setup that is pure and costly is built once per process and
  cached. The lifespan holds only what is per process: the substrate, the instruments, and the
  checks before the first request.
- **`create_server` takes the app it mounts**, so the one-origin tests mount a routed app and
  `tests/started/test_serve.py` mounts the started one.

## Considered options

- **A CPU budget for each test, like Google's test sizes.** A cost built once per process and
  cached lands on the first test that asks for it, so the budget fails a test for work it shares.
  The guard fails the cause and never depends on the order of the tests.
- **A tier for the started app, outside the default run.** A tier is for a test that needs
  something a laptop may not have. The started app needs nothing of that kind, and the tests
  that start it are few once route tests do not.
- **Share one started app across a module.** A test changes `dependency_overrides` and the
  substrate, and the next test then reads what it left. Share only what does not change.
- **Mount one resource router, never the requirement router.** A request finds its route by
  building each one before it, in order. Mounting the requirement router costs a large project
  tens of milliseconds a test, against hundreds for a start. Where that matters, a test mounts
  its resource router under the requirement's dependencies:
  `APIRouter(dependencies=tenant.router.dependencies)`, including it.

## Consequences

- A test that starts the app moves to `tests/started/`, or mounts its routers with `routed`.
- A seam that the lifespan puts on `app.state` reaches a route test only when the test names it
  to `routed`. A route test that forgets one fails where the route reads it.
- A route test does not prove the lifespan. `tests/started/` and the contract suite, which runs
  against a live backend, do.
