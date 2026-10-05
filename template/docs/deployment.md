# Deployment

Not configured, deliberately — the shape is yours. Nothing in this repository runs a deploy, so
none of what follows is checked by anything.

## The pieces

`make build` produces `frontend/dist/`, a static bundle. Serve it from any static host with an
SPA fallback. If it is served from a subpath, pass `--base=/that/path/` to `vite build`.

The backend runs under any ASGI server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

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

## Timeouts

Every wait on Postgres has a bound, and `Timeouts` in `app/store/pg.py` sets each one:

- **A statement** that runs too long is cancelled by Postgres, which raises `QueryCanceledError`.
- **A server that does not answer** at all makes asyncpg raise `TimeoutError`.
- **A transaction left open** with nothing sent is ended by Postgres. The pool replaces the
  connection.
- **A request that waits for a connection** raises `AcquireTimedOut`: every connection stayed
  in use, or a new one did not open in time.

Each one answers `500` and writes its traceback to the log. Your platform's request timeout does
not replace them. It closes the client's connection and leaves the handler running with a
connection in hand, so a database that stops answering fills the pool.

To change a bound, set it in seconds. Each applies to every route, and an unset one keeps the
value `Timeouts` ships with:

| Variable | Bounds | Ships with |
|---|---|---|
| `DATABASE_STATEMENT_TIMEOUT` | one statement | 5 |
| `DATABASE_IDLE_IN_TRANSACTION_TIMEOUT` | a transaction left open with nothing sent | 10 |
| `DATABASE_ACQUIRE_TIMEOUT` | the wait for a connection | 5 |

Raise the first two for a process that holds a connection across long work, such as a
transaction around an advisory lock. The process refuses to start on a value that is not a
number of seconds of at least a millisecond, and on any of them set without `DATABASE_URL`.

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

A field declared in `app/request_line.py`, such as `user.id`, is on `request completed` too, and
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

Add the Collector's `redaction` processor as a second layer; the application already sends only
declared attributes. Sampling beyond a fixed ratio -- keeping every error and every slow
request -- is tail sampling, which also lives in the Collector.

**What to watch.** Availability is non-5xx over all requests; latency is the share of requests
under a threshold at p95 or p99. Both come from `http.server.request.duration`, which each
backend spells its own way (`http_server_request_duration_seconds` in Prometheus). On Postgres,
`db.client.connection.count` reports the pool's connections by state (`used`, `idle`), and
`db.client.connection.max` reports the most it will open. Used near the maximum means requests
are waiting for a connection, and `AcquireTimedOut` in the log follows. Alert on
burn rate rather than on thresholds: for a 99.9% target, page at 14.4 times the budget over an
hour and five minutes, page at 6 times over six hours and thirty minutes, and open a ticket at
once over three days and six hours.

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

**Replace `tenant_for()` in `app/identity.py`, or you are serving everybody.** It ships as a
stub: every request resolves to the tenant `default`, so anyone who reaches the process reads
and writes everything it holds. This one does complain — find the line whose `message` starts
`identity:` and read its `severity`. `WARNING` means nobody has replaced it. Serving everybody
is a real thing to do for a while, behind an authenticating proxy or on an internal tool; set
`UNAUTHENTICATED_IS_INTENTIONAL=1` and the same line is reported at `INFO`. That changes a log
level and nothing else. [adr/template/0004](adr/template/0004-a-route-cannot-escape-the-identity-seam.md) says
what a replacement owes.
