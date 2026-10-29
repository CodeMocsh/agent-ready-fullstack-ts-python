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
  `database` and each value a test names on `app.state`. `routed` builds the routes it mounts
  before it returns, with `build_routes` from `app/lifespan.py`, as the lifespan does. A first
  request then builds no route on the event loop. `tests/test_routed.py` holds that.
  `test_a_route_test_assembles_what_the_app_serves` in `tests/main/test_main.py` fails when
  `create_app` serves something that `routed(*ROUTERS)` does not.
- **One shared app for each set of routers, in each process.** In a project with many routes,
  building them is most of the CPU of a route test, and the routes are the same for each test.
  `routed` builds the app once for each set of routers, and shares it with one test at a time.
  Each test gets a new `app.state` and no `dependency_overrides`, because a test changes both. A
  test may not change what the app serves: its other attributes, its routes, the routes of each router
  it includes, its middleware and its exception handlers. At the teardown of each test,
  `take_back` in `tests/routed.py` compares them with what the build made. It raises
  `SharedAppChanged`, which names the test. It ends the share too: a later read of the state of
  that app raises `SharedPastItsTest`. A test that asks twice for the same app raises
  `SharedTwiceInOneTest`. In an environment that configures telemetry, `routed` raises
  `SharedWithTelemetry`,
  because an instrumented app holds exporters for the whole process. A test that changes its app
  builds it with `assembled`, which shares nothing. `tests/test_routed.py` holds each of these.
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
- **Share the built routes, and build a new app around them for each test.** FastAPI keeps the
  routes it builds on what `include_router` adds to one app, and they point back to that app for
  `dependency_overrides`. Only the whole app can be shared without the private API of FastAPI.
- **Share the app, and trust each test to leave it as it found it.** Most of the template's own
  tests that change an app add a route or a router to it. A change that nothing checks leaks into
  every later test, in an order that changes from run to run.
- **Check where the app is built, not where it starts.** A test then starts an app that an
  earlier test or an import built, and the check does not see it.
- **Always mount one resource router, never the requirement router.** The test then does not
  assert the requirement. Each process builds the routes of a shared app once, so the routes of
  the requirement router cost a route test almost nothing.

## Consequences

- A test that starts the app moves to `tests/started/`, or mounts its routers with `routed`.
- A test that changes what its app serves builds it with `assembled`, and pays for the build.
- A value that the lifespan puts on `app.state` reaches a route test only when the test gives it
  to `routed`. A route test that leaves one out fails where the route reads it.
- A route test does not prove the lifespan. `tests/started/`, and the contract suite against a
  live backend, do.
- A lifespan that grows makes `tests/started/` slow, and nothing else. No test can hold the
  lifespan to what is needed for each start, so `AGENTS.md` carries that rule: build setup that
  is pure and costly once per process, and cache it.
