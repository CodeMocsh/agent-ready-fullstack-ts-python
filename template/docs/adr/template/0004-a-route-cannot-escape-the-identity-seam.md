# 0004. A route cannot escape the identity seam, and the seam decides nothing

Date: 2026-10-05

## Status

Accepted.

## Context

`tenant_for` in `app/identity.py` is where a request becomes a tenant. The identity stub resolves
every request to `SENTINEL_TENANT`. A project replaces it.

The obvious design lets `StoreDep` resolve the tenant, because a route that touches data needs a
store. That design fails silently. A route that takes no store resolves no tenant and answers
everybody. Nobody decides this. It comes from the dependency graph, and the next route without a
store gets the same result.

The stub is a correct default, because a fresh clone must run with no issuer and no signing key.
But from outside, the stub looks the same as a service that should authenticate and does not.

A replacement verifies a credential against a session store or a key set, so it is an
`async def`. A call to it returns a coroutine. It does not return a tenant, and it does not raise.

## Decision

- The router carries the seam, not the handler. `app/routes/tenant/__init__.py` declares
  `APIRouter(dependencies=[Depends(resolved_tenant)])`. `resolved_tenant` in `app/deps.py`
  depends on `tenant_for`, and names the tenant on the request's log line
  ([0011](0011-every-log-line-is-declared-and-written-as-json-to-stdout.md)).
- Every route under that router resolves a tenant before its handler runs, with or without a
  store. An override of `tenant_for` reaches every route under it.
- `tests/routes/test_guarantee.py` holds the rule. It overrides `tenant_for` with `refusing` from
  `tests/identity/doubles.py`. It reads the routes off the running app with `routes_of` from
  `tests/routes/walk.py`, drives each one, and fails on any route that answers.
- The test reads the routes off the app, not off a list. A list is a second place to remember,
  and the route that escapes a rule is the one that nobody added to the list.
- An exemption is an exact `(method, path)` pair in `PUBLIC_ROUTES` in that test file. It is never
  a marker on the route. The exempt routes live in `app/routes/public.py`.
- `test_the_public_list_is_exactly_the_routes_that_answer` checks the other direction. A pair
  whose route is gone fails the test, so the next route with that spelling does not get the
  exemption.
- A route is exempt only when it reads nothing that belongs to a tenant. A liveness probe
  qualifies. A status page for one tenant does not.
- Paths match whole, never by prefix. A prefix gives the exemption to every future route with that
  spelling: a prefix match on `/docs` also exempts `/docs-internal`. `generated_by_fastapi` in
  `walk.py` reads FastAPI's own schema and docs paths off the app, as exact paths.
- `tenant_for` returns a tenant or raises `Unauthenticated`. It never returns the sentinel tenant
  for a credential it cannot resolve.
- `app/main.py` answers `Unauthenticated` with `401`, `WWW-Authenticate: Bearer` and an
  `ErrorBody`, though the stub never raises it. A replacement that must add its own handler
  answers `500` until somebody notices, and a client retries a `500`.
- At startup, the lifespan calls `resolved_without_a_credential()`. It asks whether a request that
  carries nothing resolves to a tenant. It awaits an awaitable result, so an `async` replacement
  is not read as a tenant.
- Every boot logs the answer. Only the level changes:
  - `log.identity_verified` at `INFO` when the seam refuses the request.
  - `log.identity_open` at `WARNING` when the seam resolves it.
  - `log.identity_open_on_purpose` at `INFO` when `UNAUTHENTICATED_IS_INTENTIONAL` says that
    serving everybody is deliberate. `unauthenticated_is_acknowledged` in `app/wiring.py` reads
    `0`, `false`, `no` and `off` as no.
- Every log therefore answers "does this deployment authenticate?". That is the first question
  asked of a service after a leak.
- The guarantee ships with the identity stub. `test_guarantee.py`, `tests/identity/doubles.py`
  and `tests/identity/test_identity.py` test the seam as the stub ships it. The answer
  `identity_stub: false`, recorded in `.copier-answers.yml`, stops `copier update` from bringing
  them, beside `app/identity.py`.
- `tests/routes/walk.py` imports nothing about identity. Other suites that walk the routes keep
  working after a project replaces the stub.

## Considered options

- **Resolve the tenant in each handler, or through `StoreDep`.** A route that takes no store then
  answers everybody.
- **A `@public` decorator on the route.** It is tidier. But a marker travels with the commit that
  makes the mistake. Whoever adds a route without thought adds the marker in the same edit, and
  the reviewer sees one consistent change. A pair in a test file is a second, deliberate edit. It
  is the diff that a reviewer stops on.
- **A flag beside the seam, `AUTHENTICATES = False`,** that the replacer sets. A forgotten flag
  warns forever about a deployment that is correct, until somebody deletes the warning. The
  probe asks a property instead, and the property is true of any implementation.
- **A warning that cannot be acknowledged.** Serving everybody is a legitimate state: an internal
  tool, a spike, a service behind a proxy that checks identity. A deployment learns to mute a
  warning it cannot acknowledge, and then the deployment that needs the warning does not hear it.
- **Refuse to boot unless an environment variable allows it.** This breaks the deployment of every
  generated project on `copier update --defaults`, and that update must not change behaviour. A
  single-tenant tool behind a proxy that authenticates is a legitimate deployment.
- **Roles, permissions, or an organization in the path.** These are product decisions. A
  permission model is another dependency declared beside the route. When one level of access
  becomes many, the change is that dependency and a data migration, not a rewrite of every
  handler.
- **A credential verifier.** Verification needs a signing key, two lifetimes and a rotation
  policy. A template does not choose those for a project. The guarantee does not need them. It
  needs a seam that refuses, and `refusing` is that seam.

## Consequences

- No route declares `401`. The stub never raises, so the shipped build cannot emit one. A
  declared `401` describes a response that no generated project produces.
  [0009](0009-the-one-origin-entrypoint-is-the-edge.md) keeps the edge's refusals out of the
  contract for the same reason. A project that replaces the seam declares `401` and regenerates
  `openapi.json`.
- Anything finer than the tenant is enforced in code, beside a route, never in a policy.
  [0002](0002-tenant-isolation-is-forced-and-always-on.md) permits a policy to compare a column to
  a setting and nothing else. A rule per user or per project needs a join that a policy may not
  do. The database owns the tenant boundary. The route owns everything finer.
- The git hook runs the guarantee before a commit. In a project that ships
  `.github/workflows/ci.yml`, the workflow runs `make gate` again after a push, which covers a
  clone where nobody ran `make hooks`.
- A route that the walk cannot drive fails the test. It is not skipped. A websocket has no HTTP
  methods, and `test_no_route_shape_escapes_being_driven` fails on one. A mount whose routes the
  walk cannot list fails `test_every_mount_is_one_this_walk_can_see_into`. A guarantee that
  silently stops covering a route is worse than one that says so.
- A project that records `identity_stub: false` owns `test_guarantee.py` from then on. An update
  does not change it.
