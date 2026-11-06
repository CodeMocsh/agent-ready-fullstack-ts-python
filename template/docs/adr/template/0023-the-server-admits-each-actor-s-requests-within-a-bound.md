# 0023. The server admits each actor's requests within a bound

Date: 2026-11-06

## Status

Accepted.

## Context

`docs/deployment.md` said that nothing in the process limits how often a client calls, and that
the edge must. [0009](0009-the-one-origin-entrypoint-is-the-edge.md) says that `app.serve` is the
edge when nothing is in front of it. A deployment behind a Cloud Run domain mapping has no other
edge, so nothing limited a client.

In a project generated from this template, one browser tab ran a bundle that was 4 hours old. A
cycle in its cache sent about 1,200 reads a minute, and the reads of two other screens with them.
Requests took 10 s to 50 s, the host added instances, and
[0022](0022-the-connections-of-every-process-fit-inside-max-connections.md) records what the
connections did. Read budgets ([0019](0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md))
and the rule for a current read ([0020](0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md))
bound what correct code sends. Nothing bounded what a tab sends at run time. A tab with a bug,
or a tab that runs old code, is a client that the server cannot trust.

## Decision

- **Each process admits each actor within `Limits`.** An actor is the issuer and the subject that
  a credential names. `Admission` in `app/admission.py` holds an account for each actor. An actor
  may have `in_flight` requests in the process at once. It may send `burst` requests at once, and
  then `per_second` on average. It may hold `streams` streams open at once.
  `tests/admission/test_admission.py` holds each bound.
- **The key is the actor, not the principal.** A credential names the actor before anything
  resolves a tenant, and a resolver that reads the database resolves the tenant after it. So a
  project admits at the point where it verifies a credential, before any store work. It also
  admits a route that never gets a principal.
- **The numbers are the project's.** `LIMITS` in `app/admission_limits.py` is the project's list,
  as [0015](0015-the-template-owns-the-mechanism-and-the-project-owns-its-list.md) says. The
  template ships 8 requests at once, 5 a second after a burst of 40, and 4 streams. A screen's
  first draw is one request, and a focus draws each open screen again, so a person with several
  tabs open stays inside them. A tab that reads in a loop does not.
- **`in_flight` and `burst` hold the largest first draw.** Admission refuses a request over a
  bound, and does not queue it. A screen's first draw sends its reads at once, and a stream's
  request holds a request place until its response starts. So `in_flight` and `burst` are each at
  least the largest read budget in `frontend/tests/read-budgets.ts`, plus the streams that screen
  opens. A screen over them gets a `429` on its own first draw. Nothing checks this across the
  two halves.
- **`in_flight` stays under the template's pool.** One actor can then hold every request place it
  has and still leave connections for other actors. `PostgresDatabase.budget()` gives the pool.
  Nothing checks this either. When the largest first draw is larger than the pool, raise
  `max_connections`, or make the draw smaller.
- **The template admits in its identity dependency.** `resolved_principal` in `app/deps.py` calls
  `admitted`, which names the principal on the request line, then calls
  `admission.admit(request, principal.actor)`, then `audit_events.serve`. A project whose
  resolver reads the database calls `admission.admit` itself, where it verifies the credential.
  A request holds one place, so the second call takes nothing.
  `test_a_request_admitted_twice_holds_one_place` holds this.
- **A request over a bound is refused with `TooManyRequests`, and records no request event.** It
  is a `429` with a `Retry-After`: the seconds until the actor's next request fits the rate, or
  the spacing of the rate when one already fits. It is at least 1.
  `admit` and `stream` mark the request so that `RecordsRequests` records no event for it. The
  router in `app/routes/tenant/` declares the `429`, so each route under it carries it in
  `openapi.json`.
  `test_an_actor_that_spent_its_burst_waits_for_its_rate`,
  `test_a_request_over_the_bound_is_refused_and_records_no_request_event` and
  `test_every_route_that_requires_a_tenant_declares_what_the_template_refuses_it` hold this.
- **A request holds its place until its response starts, after its request event.**
  `AdmitsRequests` gives the place back when the response start leaves `RecordsRequests`, and at
  the end of a request that sent none. `assemble` adds it after `request_events.instrument`, so it
  is outside. `test_a_request_holds_its_place_until_its_request_event_is_recorded` holds this.
- **A stream holds a place of its own until its response ends.** A route whose response stays
  open calls `admission.stream(request, actor)` before it answers. Its request place comes back
  when the response starts, so a stream never holds a place among the requests.
  `test_an_open_stream_holds_a_stream_place_and_no_request_place` holds this.
- **The count is per process.** A deployment of `DATABASE_INSTANCES` processes admits up to that
  many times each limit for one actor. [0022](0022-the-connections-of-every-process-fit-inside-max-connections.md)
  makes that number a declared bound. So the rate one actor gets across the deployment is
  `per_second` times the processes that serve it.
- **An account that holds nothing and has its whole burst back is dropped.** The process sweeps
  at most once in the time a burst takes to refill, so the accounts do not grow with every actor
  that ever called.
- **Each build of the middleware stack holds one `Admission`.** `tests.routed.routed` builds a
  new stack for each test it shares an app with, so no count passes from one test to the next.

## Considered options

- **Limit at the edge only.** The deployment in the incident had no edge but the process. An edge
  also sees an address and not an actor, and many people share one address behind a proxy.
- **One count shared by every process, in Redis.** The template has no such service, and a
  request that waits for Redis has one more wait. A count per process is a bound, because the
  number of processes is a bound.
- **Admission in a middleware, before the identity seam.** The middleware does not know the
  actor, so its key is the address again.
- **Key on the principal.** A principal has a tenant, and a project may resolve the tenant from
  the database. Admission would then come after that read.
- **Admission on public routes too.** With no credential, the key is the address, and that is the
  edge's key. `POST /client-events` writes log lines only. `GET /ready` takes a connection to
  check the substrate, so the edge bounds it, or the deployment keeps it off the public origin.
- **A `Retry-After` of a fixed number of seconds.** The time until the next request fits the rate
  is known, and a fixed number is either too long or too short.
- **Queue a request over the in-flight bound.** Under a flood the queue grows, and each request
  waits behind it. That wait is what took the requests of the incident to 50 s.
- **Hold the place until the response ends.** A stream then holds a place for as long as it is
  open, and a few streams use every place an actor has.
- **Retry and back off in the client instead.** It hides a cycle and does not bound a client that
  runs old code. This decision holds whatever the client does.

## Consequences

- Under the identity stub every request is one actor, so `LIMITS` holds for everybody at once,
  in each process. A deployment that serves more than one person on the stub raises the numbers.
- A deployment of more processes admits more of one actor's requests in total. The bound across
  the deployment is `DATABASE_INSTANCES` times each limit. Choose `per_second` as the rate one
  actor may have across the deployment, divided by the processes that serve it.
- A new screen with a larger read budget, or more streams, needs `LIMITS` raised in the same
  change. Two tabs of one actor that draw at once on one process share its places.
- An actor whose requests are slow uses its places sooner. That is the case that the in-flight
  bound is for.
- A refused request still writes its `request completed` line, with the principal when it has
  one. A flood costs log volume and no database work.
- A route test that sends more requests at once than `LIMITS` admits gets a `429`.
- A route whose response stays open and that does not call `admission.stream` holds no stream
  place. Nothing detects that.
- An unauthenticated request that the identity seam refuses is not counted. Only the edge limits
  it.
