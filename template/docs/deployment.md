# Deployment

Not configured, deliberately — the shape is yours. Nothing in this repository runs a deploy, so
none of what follows is checked by anything.

## The pieces

`make build` produces `frontend/dist/`, a static bundle. Serve it from any static host with an
SPA fallback. If it is served from a subpath, pass `--base=/that/path/` to `vite build`.
Give every file under `assets/` the header `cache-control: public, max-age=31536000, immutable`.
Give every other file, `index.html` and `build.json` included, the header
`cache-control: no-cache`. Then no browser uses an `index.html` that names assets a deploy
removed. `app.serve` sets both.

`build.json` names the deployed build. An open tab reads it on a focus and when a navigation
to another path starts. When it names a build that the tab does not run, the tab reloads at the next navigation
to another path.
A host that caches it hides each new build from each open tab, and the tab keeps its old code
until a chunk goes missing. Do not serve the shell in its place: a tab reports that as a client
event of the kind `build`, once for each route. A host that still serves an old `index.html`
after a deploy makes each tab that reloaded report the same kind.
[adr/template/0025](adr/template/0025-a-tab-learns-of-a-new-build-and-reloads-at-the-next-navigation.md)
says why.

The backend runs under any ASGI server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`. On
Postgres the worker runs beside it as a process of its own, `python -m app.worker`: see
[The worker](#the-worker).

**An unset `APP_ENV` is production**, and production refuses to start without `DATABASE_URL`,
because the in-memory substrate loses every row when the process exits. `make dev`, the
contract suite and the tests set `APP_ENV=development`; a deployment sets nothing, or
`APP_ENV=production`. Any other value is refused. `app/environment.py` holds the rule and
[adr/template/0010](adr/template/0010-the-environment-is-read-in-one-place-and-run-in-another.md) says why.

Something has to strip the `/api` prefix, because the backend serves bare paths. In development
the Vite proxy does it. In a deployment, one of three:

- a reverse proxy in front of both, forwarding `/api/*` **with the prefix stripped**, which
  mirrors the dev setup;
- separate origins, with `VITE_API_BASE_URL` set at build time and `CORSMiddleware` added to
  the backend;
- one process carrying both, below.

## One process carrying both halves

```bash
cd backend
FRONTEND_BUNDLE=../frontend/dist uvicorn --factory app.serve:build_server \
  --host 0.0.0.0 --port 8000
```

`cd backend` because this half installs nothing, so `app` is importable from that directory and
nowhere else; `FRONTEND_BUNDLE` is relative to it too, and has no default, because a process
that came up on the wrong directory would answer every path with a file it never found.

**Run it behind something, not as the public edge.** Cloud Run, Fly, App Runner and an
identity-aware proxy terminate TLS and absorb the slow-client attacks a Python process should
not be meeting — so bind the `$PORT` they give you and pass **no** `--ssl-keyfile`, or the
platform's health check meets a TLS handshake and the deploy never goes green.

Whatever terminates TLS, something must. A session cookie worth setting is `Secure`, and a
browser reached over plaintext discards it, so sign-in fails by returning quietly to the
sign-in screen with nothing saying why.

With nothing in front, this process is the edge: read `SECURITY_HEADERS` and `MAX_BODY_BYTES`
in `app/serve.py` first, and [adr/template/0009](adr/template/0009-the-one-origin-entrypoint-is-the-edge.md) for
why `app.main` sets neither.

## Admission and the edge

Two limits, on two keys, in two places:

- **The process admits each actor's requests within a bound.** An actor is the issuer and the
  subject that a credential names. `LIMITS` in `app/admission_limits.py` sets how many requests
  of one actor a process serves at once, how fast it takes them after a burst, and how many
  streams it holds open for one. A request over a bound answers `429` with `Retry-After`, and
  records no request event. Its `detail`, and the line `admission refused`, name the bound.
  Every route that verifies a credential declares the `429`. A
  request holds its place until its response starts, after its request event. A stream holds a
  place of its own, which a route takes with `app.admission.stream`, until its response ends.
- **The edge limits each address.** Your platform, a CDN, a WAF or the proxy in front of this
  process. It sees a request before authentication, which the process cannot. A request with no
  credential reaches the public routes or is refused at the identity seam, and only the edge
  limits those. `GET /ready` takes a connection, so keep it off the public origin, or let the
  edge bound it.

`LIMITS` is the default. A deployment replaces any field with `ADMISSION_IN_FLIGHT`,
`ADMISSION_PER_SECOND`, `ADMISSION_BURST` or `ADMISSION_STREAMS`, and the line `admission` names
the numbers in force. Set them from three rules:

- `burst` is at least the parts of the largest first draw, plus the streams that screen opens,
  and `in_flight` at least `READS_PART_CONCURRENCY` plus those streams. Each part of `GET /reads`
  is a request, a draw sends its reads at once, and admission refuses rather than queues.
- `in_flight` under the template's pool, which the line `connections budgeted` names, leaves
  connections for other actors. Over it, other actors wait for a connection, up to
  `DATABASE_ACQUIRE_TIMEOUT`. With `max_connections=100`, `DATABASE_INSTANCES=12` and the shipped
  reserves the pool is 8, so the shipped `in_flight` of 8 fills it. When the first rule asks for more than the pool,
  raise `max_connections`, make the draw smaller, or accept the wait.
- The count is per process. A deployment of `DATABASE_INSTANCES` processes admits up to that
  many times each limit for one actor, and the [connection budget](#the-connection-budget)
  bounds that number. So choose `per_second` as the rate one actor may have across the
  deployment, divided by the processes that serve it. A shared count needs a service such as
  Redis, and this project has none.

**Measure before you enforce.** `ADMISSION_MODE=evaluate` admits each request over a bound and
writes `admission would refuse`, with the bound, for each request an enforced process would
refuse. Run a client against it, count the lines by bound, and raise the numbers until there are
none. The line `admission` is at `WARNING` while the process only evaluates. The same mode turns
refusals off when the numbers are wrong.

**A harness is a client like any other.** The contract suite sends every request as one actor.
`devtools/contract-test.sh` reads the numbers of its backend from `devtools/contract-test.env`,
which is yours. `ADMISSION_MODE=evaluate make test-contract` ends with the count by bound; set
the numbers there from it. A `429` in the suite names the bound it met.

Admission bounds one actor. The platform's concurrency for each instance (`--concurrency` on
Cloud Run) bounds the load on an instance, its most instances bound the deployment, and the
pool's wait ends at `DATABASE_ACQUIRE_TIMEOUT` with a `503`.

**Under the identity stub, every request is one actor**, so `LIMITS` holds for everybody at
once, in each process. Raise it, or replace the stub, before more than one person uses the
deployment.

A project whose resolver reads the database calls `app.admission.admit(request, actor)` where it
verifies the credential, before that read.

A request the identity seam refuses records no audit event, and neither does a public route.
Each one still writes its `request completed` line, and `POST /client-events` writes a line for
each client event it takes, so a flood costs log volume. Every request a principal makes records
one request event
([adr/template/0017](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md)).
Admission and the edge limit together are therefore the limit on how fast `audit_events`
grows.

Nothing here deletes an audit event, and the application role cannot. Retention is yours.
[Removing a tenant's audit events](#removing-a-tenants-audit-events) says how an operator
removes one tenant's events.

## A group of reads

`GET /reads` answers the parts that the browser held until its timer of 0 ms fired, each as its
route answers it alone. `LIMITS` in `app/read_limits.py` sets the bound of each part, the most parts
in one group, and how many run at once. `READS_PART_BOUND_SECONDS`, `READS_MAX_PARTS` and
`READS_PART_CONCURRENCY` each replace one, and the line `reads` names the numbers in force. The
process refuses to start when a whole group may outlast the request budget, when a group has more
parts than `ADMISSION_BURST`, or when it runs more parts at once than `ADMISSION_IN_FLIGHT`.

A group holds at most `READS_PART_CONCURRENCY` connections. A part past its bound, past the most
parts, or too large for the ceiling of the response leaves its group, and the browser sends it
again alone. The line `part left its group` names the route, the reason and the number of parts. A
part that left on its bound writes `request cancelled`. Many such lines for one route mean that
the route is slow. A line with no route names paths that are not parts, or parts past the most
parts.
[adr/template/0029](adr/template/0029-the-reads-that-start-in-one-task-are-one-request.md) says
why.

## A burst of requests

A burst is many requests that reach the backend at the same moment. One process serves them in
turn on its CPU. An instance admits them up to its concurrency limit.

A host that adds instances under load can send part of a burst to an instance that is still
starting. Cloud Run says: "If the CPU of the instance is already highly utilized, Cloud Run might
not send as many requests to a given instance"
([about concurrency](https://docs.cloud.google.com/run/docs/about-concurrency)). For each
request, it "determines if queuing for an upcoming instance or an existing instance is faster
(in that order) before trying to start a new on-demand instance"
([about instance autoscaling](https://docs.cloud.google.com/run/docs/about-instance-autoscaling)).
A request held for an upcoming instance waits until that instance is ready. Its part of the
screen draws nothing until it answers.

In one deployment, a tab that regained focus sent 18 reads in 30 ms. The warm instance took 3.
The other 15 waited 37 s for a new instance. Concurrency peaked at 17 of 40, so the limit did not
turn them away. A busy CPU fits the docs and every timestamp. The metrics did not prove it: they
report CPU per minute, and the burst arrived in 30 ms.

A screen's first draw is one request, which keeps a burst small. A tab that regains focus reads
again only the queries the screen mounted: one for its first draw, and one for each section the
person opened.
[frontend.md](frontend.md#reading-server-state) says how.

## A cold start

A request held for an upcoming instance waits for that instance's whole start. Most of a
start is the import: Python reads each module file once, one file after another.

A host that pulls the image on demand pays for each first read of a file. Cloud Run streams the
image by blocks. In one deployment the first read of a file cost about 5 ms there. The process
read 2,720 files before it served, and the import took 22 to 26 s for 8 to 10 s of CPU. Read
one after another, the same files took 13 s. Read on 8 to 32 threads, they took about 1 s, and
the import after them took 8 s, the same as its CPU. The number of files was the cost, not the
bytes and not the CPU.

Three things keep it down:

- **Import a package where it is used.** A package imported at the top of a module loads in
  every process, also in one that never calls it. `app.main` imports `app.telemetry` only when
  telemetry is configured, and `app.store.pg` imports asyncpg only when it opens a connection.
- **Compile the bytecode when you build the image.** A process that cannot write `__pycache__`
  compiles each module again at each start. `UV_COMPILE_BYTECODE=1` compiles only the packages
  that uv installs. The backend is not installed (`package = false`), so compile it as well:
  `python -m compileall app`.
- **Read the files in parallel before the import.** A process loads the same files at each
  start. Record their list when you build the image, and read them on many threads before
  uvicorn starts. This belongs in the project's image: the template ships none.

**The startup gate counts the files.** `backend/devtools/startup.py` starts a new process with
the production dependencies only and no telemetry. It builds the server as
`app.serve.build_server` does. Then it counts the files of `app`, and of the installed packages,
that the process loaded. This is the startup count. `backend/.startup-baseline.json` holds it,
and the count of each top-level package. The interpreter sets the standard library, so the count
leaves it out. The count also leaves out the files that configured telemetry adds: the exporters
and the instrumentations. The `opentelemetry` API that `app.log` reads is in the count.

- **A rise past the tolerance fails `make lint` and `make lint-check`.** The message names the
  packages that grew. Import the package where it is used. If the rise is the cost of what you
  added, record it with
  `uv run python devtools/startup.py --baseline .startup-baseline.json --update-baseline` in
  `backend/`. The new count is then in the diff, for review.
- **A fall past the tolerance fails `make lint-check`.** A baseline far above the count stops
  gating. `make lint` lowers it.

## The release step

The application verifies the schema at startup and refuses to serve if it is behind. Applying
is `make migrate`, run by something that is not the web process —
[adr/template/0003](adr/template/0003-the-application-never-applies-ddl.md). Wire it into whatever your platform
calls a release command: Fly's `[deploy] release_command`, a release or pre-deploy command on
Heroku, Railway and Render, a `Job` with `helm.sh/hook: pre-upgrade` on Kubernetes, a one-off
task on ECS, or the `migrate` service in `deploy/compose.yaml`.

It is idempotent, serialises on an advisory lock, and re-runs every entry on every call. The
application refuses to start if it can see `DATABASE_OWNER_URL`, so a single container that
migrates and then serves must drop the credential in between:
`env -u DATABASE_OWNER_URL uvicorn ...`.

**The database must have applied exactly the entries the build carries**, in both directions
([adr/template/0003](adr/template/0003-the-application-never-applies-ddl.md)). The cost, worth knowing before
your first rolling deploy: between the release step and the last old instance being replaced,
any instance of the *previous* version that restarts will not come back up. An instance that is
already running keeps answering, but its `GET /ready` fails, so a platform that probes it takes it
out of rotation. If that window matters, make the migration and the rollout one step — scale
down, migrate, scale up — and plan a rollback as a schema rollback.

`deploy/roles.sql` is committed, so it holds no password. `deploy/credentials.sh` gives
`app_app` its password from `APP_DB_PASSWORD`, as the superuser. It passes the password as a
psql variable and expands it with `:'app_password'`, which quotes it as a SQL literal. **Never
put the password into the SQL text**: a password that holds a quote then ends the statement and
starts another. psql does not expand a variable in a `-c` string, so the SQL goes in on stdin.

## Removing a tenant's audit events

An audit event outlives the row and the tenant it records, and the application role cannot
remove one ([adr/template/0017](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md)).
Where a law requires a tenant's events removed, an operator removes them as a member of
`<schema>_owner`, the role `DATABASE_OWNER_URL` names. `FORCE` binds the owner to the policy too,
so set the tenant first. For the default schema, `app`:

```sql
SET ROLE app_owner;
SET search_path TO app;
BEGIN;
SELECT set_config('app.tenant_id', 'the-tenant', true);
DELETE FROM audit_events WHERE tenant_id = 'the-tenant';
COMMIT;
```

With `DB_SCHEMA` set, put its value where `app` is in the first two lines.

The `DELETE` reports how many events it removed. It removes nothing from another tenant, because
the policy hides every other tenant's events from it.

## Writing tenant rows by hand

Postgres refuses a write to a tenant table that names no actor, and records every write it
allows ([adr/template/0017](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md)).
An operator who corrects a row by hand sets their own actor first, in the same session:

```sql
SELECT set_config('app.actor_issuer', 'operators', false),
       set_config('app.actor_subject', 'the-operator', false),
       set_config('app.actor_name', 'The operator', false);
```

Removing audit events needs no actor: `audit_events` records nothing of its own.

## Timeouts

Every wait on Postgres has a bound, and `Timeouts` in `app/store/pg.py` sets each one:

- **A statement** that runs too long is cancelled by Postgres, which raises `QueryCanceledError`.
- **A server that does not answer** at all makes asyncpg raise `TimeoutError`.
- **A transaction left open** with nothing sent is ended by Postgres, and raises
  `ConnectionLost`. The pool replaces the connection.
- **A request that waits for a connection** raises `AcquireTimedOut`: every connection stayed
  in use, or a new one did not open in time.

The first two answer `500` and write a traceback to the log. A request that gets no connection, or
whose connection ends, answers `503` with `Retry-After`, and writes the line
`database unavailable`, which names the refusal and the driver's error. A transaction left open
is one of those: asyncpg reports it as a connection that ended. The
[connection budget](#the-connection-budget) says why. Your platform's request timeout does
not replace them. It closes the client's connection and leaves the handler running with a
connection in hand, so a database that stops answering fills the pool.

To change a bound, set it in seconds. Each applies to every route and to every task beside the
requests, and an unset one keeps the value `Timeouts` ships with:

| Variable | Bounds | Ships with |
|---|---|---|
| `DATABASE_STATEMENT_TIMEOUT` | one statement | 5 |
| `DATABASE_IDLE_IN_TRANSACTION_TIMEOUT` | a transaction left open with nothing sent | 10 |
| `DATABASE_ACQUIRE_TIMEOUT` | the wait for a connection | 5 |

Each wait in a request has a bound under the request budget, `REQUEST_BUDGET_SECONDS` in
`app/waits.py`. So the process refuses to start when an answer (`DATABASE_STATEMENT_TIMEOUT` plus
a second) or a connection (`DATABASE_ACQUIRE_TIMEOUT`) may take as long as the budget. Do not
raise either for long work. Run that work beside the requests. The pool's answer bound applies to
every call, so give each long call its own `timeout=` in asyncpg. In its transaction, set
`SET LOCAL statement_timeout`, and `SET LOCAL idle_in_transaction_session_timeout` when the
transaction stays open across long work, such as a transaction around an advisory lock. The
process also refuses to start on a value that is not a number of seconds of at least a
millisecond, and on any of them set without `DATABASE_URL`.

## The connection budget

Postgres refuses a connection past `max_connections`. Every process of every instance holds
connections, so instances added under load can take the server past it. Then a process that
cannot open a connection fails in ways it did not plan for.

So each process gets an equal share, and the template's pool takes what remains of it:

```
share = (max_connections - reserved - OUTSIDE) / DATABASE_INSTANCES
pool  = share - IN_PROCESS
```

- `reserved` is what the server keeps for other roles: `superuser_reserved_connections`, and
  `reserved_connections` from Postgres 16. The pool reads both, and `max_connections`, from the
  server before it opens.
- `DATABASE_INSTANCES` is the most processes that hold connections at once. It is not your
  platform's most instances. During a rollout, the old revision serves its open requests while
  the new one starts, so a rollout that overlaps every instance doubles it. Cloud Run with
  `--max-instances=6`, one process in each instance, therefore sets `DATABASE_INSTANCES=12`.
  Count each worker process too: they hold pools of the same size. Nothing can check this number
  against your platform. Production refuses to start without it, and the development loop on
  Postgres is two processes.
- `IN_PROCESS` is `TEMPLATE_IN_PROCESS` in `app/store/budget.py`, the worker's `LISTEN`
  connection, and what `app/connection_budget.py` names: each pool or connection one process
  holds beside the template's pool, such as a second pool. Size each from its entry there. Both
  processes take the same share, so the web process keeps the worker's `LISTEN` connection and
  does not use it.
- `OUTSIDE` names each holder that is not one of the processes: the release step, an operator's
  session.

The line `connections budgeted` names each number. A pool that would get no connection refuses
to start, and the message names what to lower.

When the pool has no free connection within `DATABASE_ACQUIRE_TIMEOUT`, or the server refuses a
new one, that one request answers `503` with `Retry-After`. That includes the connection that
records its request event. Every route that verifies a credential declares it. The request
records no [request event](adr/template/0017-the-database-records-every-write-and-the-request-layer-every-request.md),
because recording it would wait for a connection too.
[adr/template/0022](adr/template/0022-the-connections-of-every-process-fit-inside-max-connections.md)
says why.

## The worker

Work that may outlast the request budget is a job. A route asks for it and answers `202`, and the
worker process, `python -m app.worker`, ends it. It claims jobs from Postgres and serves no
request. The web process starts no job loop. `docs/backend.md` says how to write a job, and
[adr/template/0027](adr/template/0027-work-that-outlives-its-request-is-a-job-that-a-worker-process-claims.md)
says why.

- **Run the worker as a service of its own,** with the image of the web process and the same
  `DATABASE_URL`, `DB_SCHEMA` and `DATABASE_INSTANCES`. It needs no port and no ingress. On
  Cloud Run, a worker pool. Two processes in one container share its CPU, and a platform that
  scales on CPU then starts instances for requests because of the work of jobs.
- **Give the worker CPU while no request is open.** It claims jobs while nobody has the app
  open. On Cloud Run, instance-based billing. The web process no longer needs it.
- **Keep one worker running at least.** When no worker runs, each job stays `pending`. Alert on
  the age of the oldest job that is ready: `SELECT min(ready_at) FROM job_claims WHERE state =
  'ready'`.
- **A worker that stops loses no job.** SIGTERM and SIGINT stop each attempt. Its lease ends
  after `Leases.lease`, and another worker claims the job. Each claim counts one attempt. A
  claim past `MAX_ATTEMPTS` ends the job `dead`, with the failure `AttemptsSpent`.
- **A worker waits out a database outage.** A restart, a failover or a proxy that stops ends each
  connection. Each loop of the worker waits the refusal's `Retry-After` and starts again, and the
  line `loop waits for the database` names the loop. The `LISTEN` connection opens again and
  wakes each slot to look. An attempt whose connection ends is deferred, as below. A step of an
  attempt runs again until its lease would end, then the line `attempt abandoned` names the
  attempt. When the database stays unavailable for `UNAVAILABLE_WITHIN_SECONDS`, 300 s, the
  worker stops with `DatabaseGone`. Let the platform start it again: a new instance brings back
  a proxy that stopped.
- **The worker accepts a longer bound on a statement.** A `DATABASE_STATEMENT_TIMEOUT` whose
  answer reaches `REQUEST_BUDGET_SECONDS` starts a worker, and refuses a web process.
- **An attempt that the database did not serve does not count.** It got no connection, or its
  connection ended. The job is ready again after the refusal's `Retry-After`, and the line
  `database unavailable` names the refusal. When the write that makes it ready is not served
  before the lease would end, the lease ends, and the next claim counts. A pool
  too small for `AT_ONCE` handlers and their renewals defers jobs again and again: watch the
  line.
- **The worker refuses the in-memory substrate.** Without `DATABASE_URL`, the web process runs
  each job itself, and no other process can see them.
- **Watch the line `job dead`.** It names the kind, the job and the failure. Each attempt that
  fails writes the WARNING line `job failed`, with its traceback. An attempt whose lease ended
  writes `lease lost`.

## Work beside the requests

Each wait in a request on a system outside the process has a bound under the request budget.
`app.waits.waiting` bounds each such wait, and refuses one in a request whose bound is not
under the budget. Work that waits longer is a job, as above. Work the caller does not wait for,
and that ends within the budget, starts with `app.waits.beside(work, name=..., within=...)`.
`docs/backend.md` says how to write it.

- **Alert on a request slower than `REQUEST_BUDGET_SECONDS`, and keep the threshold equal to
  the constant.** The gate catches each wrapped wait that a route test runs. The alert catches
  the rest: a wait nobody wrapped, a wrapped wait no route test runs, and waits that each have a
  bound under the budget and add up past it. Find the wait, and put it in `waiting` or beside the
  request.
- **A platform that throttles CPU between requests starves the work.** Cloud Run with
  request-based billing gives an instance CPU only while a request is open, so work beside the
  requests that runs after the response stalls until the next request. Keep it short, or make it
  a job.
- **Work beside the requests ends with its process.** At shutdown the lifespan cancels each task
  that still runs, and the WARNING line `work beside the requests cancelled` names it. A task
  that does not end within `CANCELLED_WITHIN_SECONDS` of the cancel fails the shutdown with
  `BesideOutlivedItsCancel`, which names it. The lifespan then closes the database within
  `CLOSED_WITHIN_SECONDS`. The result of the work is lost. Work whose loss matters is a job. A
  task that raises, or that outlasts `within`, writes the ERROR line
  `work beside the requests failed`, with its traceback.
- **Shared work ends with its process too.** At shutdown the lifespan cancels each run still in
  flight, and each caller that awaits it is cancelled. The WARNING line `shared work cancelled`
  names the run. A run that raises writes the ERROR line `shared work failed`, with its
  traceback.

## Logs

The backend writes one JSON object per line to stdout, and nothing else. Every cloud's container
runtime collects stdout with no agent and no SDK, so there is nothing to configure in the
application. The names are the ones Cloud Logging reads as they are, and the HTTP fields follow
the OpenTelemetry semantic conventions.
[adr/template/0011](adr/template/0011-every-log-line-is-declared-and-written-as-json-to-stdout.md) says why every
line is declared.

| Field | What it holds |
|---|---|
| `time` | ISO 8601, UTC |
| `severity` | `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `message` | a constant sentence; values go in fields of their own |
| `logger` | `app` for this project's lines, a library's name for its own |
| `service.version` | the `service.version` in `OTEL_RESOURCE_ATTRIBUTES`; null when it names none |
| `request_id` | while a request is served; the response carries it as `X-Request-ID` |
| `trace_id`, `span_id` | while a traced request is served; the ids its spans carry |
| `exception` | the whole traceback, when there is one |
| `tenant_id` | on `request completed`; null on a public route, or when the identity seam refused |
| `actor.issuer`, `actor.subject` | on `request completed`; who the request acted as, null when `tenant_id` is |

A field declared in `app/request_line.py`, such as `order.id`, is on `request completed` too, and
null when the request did not name it.

**Set `OTEL_RESOURCE_ATTRIBUTES=service.version=<your build>` on every deployment**, with or
without a Collector. A session that spans a deploy is read by version, and a line with none
cannot be placed. An entry that is not `key=value` refuses to start.

**What each cloud does with it:**

- **GCP.** Cloud Logging parses each line into `jsonPayload` and reads `severity` and `message`.
  Error Reporting groups on `exception` at no charge.
- **AWS.** CloudWatch Logs Insights finds the fields at query time, on the Standard log class
  only. **A log group keeps its data forever until you set a retention period**, so set one.
  A data protection policy on the group masks emails, card numbers and credentials as they
  arrive. It is billed per GB scanned, and it is worth having as a second layer.
- **Azure.** Container Apps stores the line as one string. Read it with `parse_json` in KQL.

**A line names its trace only by `trace_id` and `span_id`.** No cloud's console opens a line from
a trace by those fields alone: Cloud Logging links only by its own `logging.googleapis.com/trace`
fields, and CloudWatch and Azure Monitor link stdout by nothing. Find a trace's lines by
`trace_id` instead. X-Ray writes the same id as `1-`, its first 8 hex digits, `-` and the other
24; Application Insights calls it `operation_Id`. A project that wants the one-click link adds its
cloud's fields itself, because this application names no vendor --
[adr/template/0012](adr/template/0012-traces-and-metrics-leave-over-otlp-to-a-collector-the-deployment-owns.md).

**Retention is yours to set, and shorter is safer.** The log holds personal data even when
nobody meant it to: an exception's message is written as the library wrote it. 14 to 30 days
suits operational logs. When you add security events, PCI DSS asks for 12 months.

**The platform keeps a request log of its own, and nothing here controls it.** Cloud Run writes
every request to `run.googleapis.com/requests`, with the full URL and its query string, the
client IP and the user agent. An AWS load balancer's access log, and an Azure Front Door or
Application Gateway access log, hold the same once they are on. A value this application keeps
out of its own lines is still in that log when it was in the URL. A deployment that must keep
values out of its logs keeps them out of URLs, and excludes that log or gives it a short
retention: an exclusion filter on Cloud Logging's `_Default` sink, a lifecycle rule on the S3
bucket the access logs go to, a retention on the Log Analytics table.

**Browser failures arrive here too**, as lines whose `message` is `client event` and whose
`source` is `client`. Anybody can post one, so read them as a report and never as evidence.

## Traces and metrics

Off until `OTEL_EXPORTER_OTLP_ENDPOINT` names a Collector, which the application posts to over
OTLP/HTTP. [adr/template/0012](adr/template/0012-traces-and-metrics-leave-over-otlp-to-a-collector-the-deployment-owns.md)
says why the application stops there.

| Variable | What it does |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | The Collector, e.g. `http://localhost:4318`. Unset, nothing is instrumented. |
| `OTEL_SERVICE_NAME` | Required with the endpoint; every span and metric is filed under it. |
| `OTEL_TRACES_SAMPLER_ARG` | The share of new traces kept, 0 to 1. Every trace when unset. |
| `TRUST_INBOUND_TRACE_CONTEXT` | `1` continues a caller's trace. Unset, a caller's trace is a link. |

`OTEL_SERVICE_NAME`, the ratio, trust or `OTEL_EXPORTER_OTLP_HEADERS` without the endpoint
refuses to start. Beside the endpoint, so does a variable the SDK would honour and this
process does not -- a
per-signal endpoint, a sampler, exporter or propagator choice, `OTEL_SDK_DISABLED`, header
capture, or a protocol other than `http/protobuf`. The SDK reads `OTEL_RESOURCE_ATTRIBUTES` for
`service.version`, the same value every log line carries, and for labels such as
`deployment.environment`, and its own `OTEL_EXPORTER_OTLP_HEADERS`, `_TIMEOUT` and
`_CERTIFICATE` for a Collector that needs them.

**Run the Collector beside the application, and point it at your destination.**

- **GCP.** Google's Collector build as a Cloud Run sidecar, or on GKE, exporting to
  `telemetry.googleapis.com` with the service account's credentials.
- **AWS.** The AWS Distro for OpenTelemetry as an ECS sidecar, exporting traces to X-Ray and
  metrics to CloudWatch over OTLP with SigV4. Application Signals then builds a service map and
  SLOs from the spans.
- **Azure.** The Container Apps managed agent, or a Collector, exporting to Azure Monitor. It
  needs delta temporality, which the Collector's `cumulativetodelta` processor provides.
- **Anything else** that takes OTLP: Grafana, Jaeger, Datadog, Honeycomb.

**Only a request starts a trace.** A query is a span only inside a traced request. A query in a
probe, or in work outside any request, exports no span. A probe still writes its `request
completed` line. The pool metrics still count the connections that work uses.

Add the Collector's `redaction` processor as a second layer; the application already sends only
declared attributes. Sampling beyond a fixed ratio -- keeping every error and every slow
request -- is tail sampling, which also lives in the Collector.

**What to watch.** Availability is non-5xx over all requests; latency is the share of requests
under a threshold at p95 or p99. Both come from `http.server.request.duration`, which each
backend spells its own way (`http_server_request_duration_seconds` in Prometheus). On Postgres,
`db.client.connection.count` reports the pool's connections by state (`used`, `idle`), and
`db.client.connection.max` reports the most it will open. Used near the maximum means requests
are waiting for a connection, and `AcquireTimedOut` in the log follows.
`db.client.connection.wait_time` reports how long each wait for a connection took, in seconds:
the waits that ended in a connection, and the ones that ended in `AcquireTimedOut`. Each wait
includes one round trip to Postgres. A p95 near that round trip means requests do not queue for
a connection. A p95 that rises with load while the CPU and the event-loop delay stay flat
means requests queue for the pool. Give the pool more of the [connection budget](#the-connection-budget), or hold each
connection for less time. A p95 near `DATABASE_ACQUIRE_TIMEOUT` means waits end in
`AcquireTimedOut` and a `503`. The log counts those exactly. Alert on
burn rate rather than on thresholds: for a 99.9% target, page at 14.4 times the budget over an
hour and five minutes, page at 6 times over six hours and thirty minutes, and open a ticket at
once over three days and six hours.

**A held event loop.** A held loop delays every request on the instance. The WARNING line
`event loop held` names a loop held in CPU, and where it was held. Make a log-based metric that
counts that line, and alert on the metric. Do not alert on a p99 of `asyncio.event_loop.delay`:
a short hold is one sample among hundreds a minute. The delay histogram shows the trend. A
platform that throttles CPU between requests, such as Cloud Run with request-based billing, runs
the loop late while nothing holds it. There the delay rises on an idle instance, and the line
stays silent. The line also stays silent when CPU work in another thread holds the interpreter
lock. Only the delay rises then, on an instance that serves requests.

**Probes.** `/health` answers while the process does; point liveness at it. `/ready` answers
once the substrate does and its schema is current; point readiness at it, so a database outage
takes an instance out of rotation instead of restarting it. Under `app.serve` both sit under
`/api`. Neither is traced or measured.

`make observe` runs Grafana, Tempo and Prometheus locally and prints what to export.

## Three ways to ship something broken

**Never ship mock mode.** A production build must leave `VITE_ENABLE_MSW` unset, which is why
there is no `.env.production` to set it in.

**Set `DATABASE_URL`, or you are deploying the in-memory substrate.** It resets on every restart
and nothing complains: the app comes up, serves, and loses the data. It logs which substrate it
came up on — find the line whose `message` is `serving` and read its `substrate`, or assert
`app.state.database.name` from a smoke test.

**Replace `principal_for()` in `app/identity/__init__.py`, or you are serving everybody.** It ships
as a stub: every request resolves to the tenant `default`, so anyone who reaches the process reads
and writes everything it holds. This one does complain — find the line whose `message` starts
`identity:` and read its `severity`. `WARNING` means nobody has replaced it. Serving everybody is a
real thing to do for a while, behind an authenticating proxy or on an internal tool; set
`UNAUTHENTICATED_IS_INTENTIONAL=1` and the same line is reported at `INFO`. That changes a log
level and nothing else. Under the stub every request to a tenant route resolves to a principal, so
anybody who reaches the process writes one audit event for each such request, and everybody
shares one actor's [admission](#admission-and-the-edge). Put a limit at the edge before anybody else
can reach it.
[adr/template/0004](adr/template/0004-a-route-cannot-escape-the-identity-seam.md) says what a
replacement owes.

## Response bytes

The default finite response limit is `RESPONSE_MAX_BYTES`, initially 4194304 bytes.
A route can declare a different limit through `response_limits.allow`.
The bundled-file route declares the deployment ceiling so large assets need no global default increase.
`RESPONSE_CEILING_BYTES`, initially 16777216 bytes, caps every finite route exception.
Set the ceiling below your edge's response limit. Allow headroom for encoding changes.
Both settings must be integer byte counts of at least 1024. The default cannot exceed the ceiling.
Invalid settings stop application construction.

[Cloud Run's limit](https://docs.cloud.google.com/run/quotas#request_limits_for_cloud_run)
is 32 MiB for HTTP/1 responses without chunked delivery or streaming.
It is not a limit for every transport or deployment.
The template uses a smaller ceiling by default and does not hard-code Cloud Run into route policies.

A `ResponseTooLarge` refusal answers `500` before any success body leaves the guard.
The `response too large` log names the limit and observed bytes without logging the body.
Buffering starts at response start and must finish within the existing request budget.
A buffer timeout answers the named `ResponseTimedOut` refusal. Handler work before response start keeps its existing wait policy.
The existing 5xx signal can alert on these refusals. Verify that signal in your deployed monitoring system.
A route exception needs a code reason. A stream needs an explicit declaration and its own resource bounds.
[adr/template/0028](adr/template/0028-every-finite-response-has-a-byte-bound.md) defines both contracts.

Finite buffering costs up to the selected limit per active response, plus application allocations and transport copies.
Choose concurrency and memory together. This limit does not bound serialization work or decompressed content.
