# 0004. A route cannot escape the identity seam, and the seam decides nothing

Date: 2026-10-05

## Status

Accepted.

## Context

`principal_for` in `app/identity/__init__.py` is where a request becomes a principal: a tenant
and an actor. The identity stub resolves every request to `SENTINEL_PRINCIPAL`. A project
replaces it.

The obvious design lets `StoreDep` resolve the tenant, because a route that touches data needs a
store. That design fails silently. A route that takes no store resolves no principal and answers
everybody. Nobody decides this. It comes from the dependency graph, and the next route without a
store gets the same result.

The stub is a correct default, because a fresh clone must run with no issuer and no signing key.
But from outside, the stub looks the same as a service that should authenticate and does not.

A replacement verifies a credential against a session store or a key set, so it is an
`async def`. A call to it returns a coroutine. It does not return a principal, and it does not
raise.

A project that replaces the resolver stops receiving the stub. Anything the template shares with
every resolver must then live in a module the project does not replace.

## Decision

- The router carries the seam, not the handler. `app/routes/tenant/__init__.py` declares
  `APIRouter(dependencies=[Depends(resolved_principal)])`. `resolved_principal` in `app/deps.py`
  depends on `principal_for`, and names the tenant and the actor on the request's log line
  ([0011](0011-every-log-line-is-declared-and-written-as-json-to-stdout.md)).
- Every route under that router resolves a principal before its handler runs, with or without a
  store. An override of `principal_for` reaches every route under it.
- `tests/routes/test_guarantee.py` holds the rule. It overrides `principal_for` with `refusing` from
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
- `principal_for` returns a principal or raises `Unauthenticated`. It never returns the sentinel
  principal for a credential it cannot resolve. That fallback hands one tenant's data to anybody
  who failed to log in.
- A principal is a tenant and an actor. An actor is named by `issuer` and `subject` together,
  because a subject is unique only within its issuer.
  `test_an_actor_is_named_by_its_issuer_and_subject_together` holds this. An actor that acts for a
  person names that person in `on_behalf_of`.
- `app/identity/shared.py` is the template's, and an update brings it to every project. It holds
  what every resolver shares: `Principal`, `Actor`, `Unauthenticated`, the sentinel principal and
  the probe. `app/identity/__init__.py` holds `principal_for` and re-exports the shared names.
  `test_app_identity_defines_none_of_the_mechanism_itself` fails on a project that defines a
  shared name itself
  ([0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md)).
- `app/main.py` answers `Unauthenticated` with `401`, `WWW-Authenticate: Bearer` and an
  `ErrorBody`, though the stub never raises it. A replacement that must add its own handler
  answers `500` until somebody notices, and a client retries a `500`.
- At startup, the lifespan calls `resolved_without_a_credential` with the seam the app serves
  with: `principal_for`, or what `app.dependency_overrides` puts in its place. It asks whether a
  request that carries nothing resolves to a principal. It awaits an awaitable result, so an
  `async` replacement is not read as a principal.
- Every boot logs the answer. Only the level changes:
  - `log.identity_verified` at `INFO` when the seam refuses the request.
  - `log.identity_open` at `WARNING` when the seam resolves it.
  - `log.identity_open_on_purpose` at `INFO` when `UNAUTHENTICATED_IS_INTENTIONAL` says that
    serving everybody is deliberate. `unauthenticated_is_acknowledged` in `app/wiring.py` reads
    `0`, `false`, `no` and `off` as no.
- Every log therefore answers "does this deployment authenticate?". That is the first question
  asked of a service after a leak.
- The guarantee ships with the identity stub. `test_guarantee.py`, `tests/identity/doubles.py`,
  `tests/identity/test_identity.py` and `tests/started/test_identity.py` test the seam as the
  stub ships it. The answer
  `identity_stub: false`, recorded in `.copier-answers.yml`, stops `copier update` from bringing
  them, beside `app/identity/__init__.py`. `tests/identity/test_shared.py` tests the shared names,
  and reaches every project.
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
- **One file, `app/identity.py`, for the resolver and what it shares.** A project that replaces
  the resolver then stops receiving the shared names too. `Unauthenticated` and the sentinel
  stop changing for it, and the template can add nothing to identity that reaches it.
- **A `user_id` beside the tenant.** An actor may be an app that acts for a person, and a user id
  says neither. A subject without its issuer names two people when two issuers vouch for it.
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
