# 0018. A route test never starts the app

Date: 2026-10-27

## Status

Accepted.

## Context

The lifespan of `create_app` is the startup of the process. It verifies the substrate, builds
every route before the first request, says what the deployment authenticates, and runs what the
project adds there. A project adds to it as it grows: a compiled pipeline, a validated spec, a
client for each connector.

A route test asserts the HTTP contract, and needs none of that. The obvious fixture is
`with TestClient(create_app())` in each test, and it runs the whole lifespan in each route test.
In the template a start costs almost nothing. In a project that grows, the starts can use most of
the CPU of the suite, and few of those tests use what the lifespan built. No test fails while the
cost grows.

This is the *General Fixture* that Meszaros names as a cause of *Slow Tests*: a fixture built for
each test, which holds more than any one test uses. The fix is a smaller fixture. `TestClient`
runs the lifespan only inside `with`, so an app that is not started still serves a request.

## Decision

- **Three layers.** A unit test calls a function. A **route test** starts nothing, and usually
  mounts the routers under test with `tests.routed.routed`. A test of the **started app** runs
  the lifespan of `create_app`, and lives in `tests/started/` or in a tier.
- **One assembly.** `assemble` in `app/main.py` puts the refusal of an unresolved tenant, the
  request event, the request line and the telemetry around each route. `create_app` is
  `assemble(*ROUTERS, lifespan=lifespan)`. `routed` is `assemble(*routers, lifespan=None)`, with
  `database` and each value a test names on `app.state`.
  `test_a_route_test_assembles_what_the_app_serves` in `tests/main/test_main.py` fails when
  `create_app` serves something that `routed(*ROUTERS)` does not.
- **The guard.** `tests/conftest.py` replaces the lifespan when it is imported. The replacement
  checks the running test when the lifespan starts, not when the app is built. Outside
  `tests/started/` and the tier folders, it raises `StartedOutsideItsFolder`, which names the
  test and where it belongs. An app built at import is checked the same way.
  `tests/test_started.py` holds the guard to that.
- **A started app lives for one test.** When a test stops an app that another test started, the
  replacement raises `StartedForMoreThanOneTest`. A fixture wider than one test therefore cannot
  share a started app. `tests/test_started.py` holds this too.
- **A fresh substrate for each test.** The `database` fixture is a new `MemoryDatabase` for each
  test.
- **Other values on `app.state` come from one fixture.** The `app_state` fixture holds each value
  beside the database that the lifespan puts on `app.state`. It is empty in the template. A
  project overrides it in `tests/routes/conftest.py`, where `tests/routes/test_guarantee.py`
  reads it.
- **`create_server` takes the app it mounts.** `tests/serve/test_serve.py` mounts an app that
  `routed` builds. `tests/started/test_serve.py` mounts the app that `create_app` builds, and
  fails when the server does not run its lifespan.

## Considered options

- **A CPU budget for each test, like the test sizes of Google.** A cost that is cached once per
  process falls on the first test that asks for it. The budget then fails a test for work that
  other tests share. The guard fails the cause, and does not depend on the order of the tests.
- **A tier for the started app, outside the default run.** A tier is for a test that needs
  something a laptop may not have. The started app needs nothing of that kind, and few tests
  start it when route tests do not.
- **One started app for each module.** A test changes `dependency_overrides` and the substrate,
  and the next test then reads what it left. Share only what does not change.
- **Check where the app is built, not where it starts.** A test then starts an app that an
  earlier test or an import built, and the check does not see it.
- **Always mount one resource router, never the requirement router.** A request builds every
  route in each router it checks, up to the router that holds its route. For most projects the
  cost of a requirement router is small next to a start. Where it is not, a test mounts the
  resource router under the dependencies of the requirement. `docs/backend.md` shows how.

## Consequences

- A test that starts the app moves to `tests/started/`, or mounts its routers with `routed`.
- A value that the lifespan puts on `app.state` reaches a route test only when the test gives it
  to `routed`. A route test that leaves one out fails where the route reads it.
- A route test does not prove the lifespan. `tests/started/`, and the contract suite against a
  live backend, do.
- A lifespan that grows makes `tests/started/` slow, and nothing else. No test can hold the
  lifespan to what is needed for each start, so `AGENTS.md` carries that rule: build setup that
  is pure and costly once per process, and cache it.
