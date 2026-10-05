# A route cannot escape the identity seam, and the seam decides nothing

`app/identity.py::tenant_for` is where a request becomes a tenant. Three things follow, and
each of them is a decision a reasonable person would undo.

## The router carries it, not the handler

`router = APIRouter(dependencies=[Depends(tenant_for)])`. Every route declared on it resolves a
tenant before its handler runs, whether or not it asks for a store.

The obvious arrangement is the other one: let `StoreDep` resolve the tenant, since a route that
touches data needs a store anyway. That is what this did, and it is wrong in a way that reads
as correct. **A route that takes no store resolves nothing and answers everybody.** The sibling
project this template was extracted alongside shipped exactly that — an endpoint serving a
constant, needing no store, answering `200` to a request with no credential while every route
beside it answered `401`. Nobody decided that. It fell out of the dependency graph, and the
next route needing no store would have inherited it just as quietly.

Declaring it on the router costs a generated project nothing today, because `tenant_for` ships
as a stub that resolves everybody. What it buys is that the day somebody replaces that
function, every route already goes through it — including the ones written in between.

`tests/routes/test_guarantee.py` substitutes a seam that refuses, reads the routes off the
running app, drives each one, and fails on any that answers. Read off the app rather than
listed, because a list is a second place to remember and the route that escapes a rule is
exactly the one nobody remembered to add to it.

## An exemption is a named pair in a test, never a marker on the route

`GET /health` answers without a tenant. It is named in `PUBLIC_ROUTES` in that test file, as an
exact `(method, path)` pair.

A `@public` decorator is tidier and is what somebody will propose next. It is also the wrong
shape: **a marker travels with the commit that made the mistake.** Whoever adds a route without
thinking adds the marker in the same edit, and review sees one self-consistent change. A name
on a list in a test file is a second, deliberate edit that reads as somebody claiming an
exemption, and it is the diff a reviewer stops on.

Exact pairs, never a prefix. A prefix hands the exemption to every future route spelled that
way. This was got wrong once here in a place nobody was looking: the filter for FastAPI's own
`/openapi.json` and `/docs` was a `startswith`, which silently exempted `/docs-internal`. Those
paths are now read off the app and matched whole.

An exemption is legitimate when the route reads nothing that belongs to anybody. A liveness
probe qualifies. A per-tenant status page is not a probe.

## A deployment that authenticates nothing says so, and can say it means to

The stub is a correct default — a fresh clone must run with no issuer and no signing key — and
it is indistinguishable from outside from a service that was meant to have authentication and
never got it. Boot is where that difference gets said: `INFO` when the seam refuses a
credential-less request, `WARNING` when it resolves one, and `INFO` again when
`UNAUTHENTICATED_IS_INTENTIONAL` records that serving everybody is deliberate.

**The fact is logged either way; only the level moves.** Serving everybody for a while is a
real thing to be — an internal tool, a spike, a service behind an identity-aware proxy — and a
warning that cannot be acknowledged is one a deployment learns to mute, which costs the
deployment that needed to hear it. Every log still answers "does this authenticate?", which is
the first question asked of a service that turns out to have leaked.

## Considered options

**Resolve the tenant in each handler.** Rejected above; it is what shipped the public endpoint.

**A flag beside the seam** — `AUTHENTICATES = False`, flipped by whoever replaces it. Rejected.
The flag that is forgotten warns forever about a deployment that is fine, until somebody
deletes the warning, which is the line that most needed keeping. What is asked instead is a
property true of any implementation: *if a request carrying nothing resolves to a tenant, this
deployment authenticates nobody.*

That probe must **await** an awaitable result. Verifying a credential reaches a session store,
so `async def tenant_for` is the shape a replacement takes, and calling one returns a coroutine
rather than raising. Read as a tenant, that told a fully authenticating deployment it
authenticated nothing, on every boot, naming a coroutine object. Every test that substituted
only a synchronous seam passed straight over it.

**Refuse to boot** rather than warn, with an environment variable to allow it. Rejected twice
over. It would break every already-generated project's deployment on `copier update --defaults`,
which this template requires to be behaviour-preserving; and a single-tenant tool behind an
authenticating proxy is a legitimate deployment that a template has no business refusing.

**Ship roles, permissions, or an organization in the path.** Rejected as product decisions. The
single check point is what keeps the door open: a permission model arrives as another
dependency declared beside the route, and the day a rung becomes a grid it is that dependency
and a data migration rather than a rewrite of every handler.

**Ship a credential verifier.** Rejected. Verifying means a signing key, two lifetimes and a
rotation policy, none of which a template picks on a project's behalf — and none of which the
guarantee depends on. What the guarantee needs is something that refuses, which lives in
`tests/doubles.py` and is four lines.

## Consequences

`401` is **not** declared on any route, and that is deliberate. The build that ships cannot
emit one, because the stub never raises; declaring it would describe a response no generated
project produces, which is the reasoning [0006](0006-the-one-origin-entrypoint-is-the-edge.md)
gives for keeping the edge's own refusals out of the contract. A project that replaces the seam
adds it and regenerates.

Anything finer than the tenant is enforced in code, beside a route, and never in a policy.
[0002](0002-tenant-isolation-is-forced-and-always-on.md) permits a policy to compare a column
to a setting and nothing else, so a per-user or per-project rule needs a join it may not do.
The tenant boundary is the database's; everything below it is the route's.

The guarantee is checked twice: by the git hook before a commit, and by
`.github/workflows/ci.yml` after a push, which runs the same target against a fresh checkout.
The second is what covers a clone where `make hooks` was never run.

A route shape the test cannot drive is refused rather than skipped. A websocket has no HTTP
methods, so it cannot be driven as `(method, path)`; `test_no_route_shape_escapes_being_driven`
fails on one instead of dropping it, because a guarantee that quietly stops covering a route is
worse than one that says it does not.
