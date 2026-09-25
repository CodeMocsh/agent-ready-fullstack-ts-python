# Deployment

Not configured, deliberately — the shape is yours. Nothing in this repository runs a deploy, so
none of what follows is checked by anything.

## The pieces

`make build` produces `frontend/dist/`, a static bundle. Serve it from any static host with an
SPA fallback. If it is served from a subpath, pass `--base=/that/path/` to `vite build`.

The backend runs under any ASGI server: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

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
in `app/serve.py` first, and [adr/0006](adr/0006-the-one-origin-entrypoint-is-the-edge.md) for
why `app.main` sets neither.

## The release step

The application verifies the schema at startup and refuses to serve if it is behind. Applying
is `make migrate`, run by something that is not the web process —
[adr/0003](adr/0003-the-application-never-applies-ddl.md). Wire it into whatever your platform
calls a release command: Fly's `[deploy] release_command`, a release or pre-deploy command on
Heroku, Railway and Render, a `Job` with `helm.sh/hook: pre-upgrade` on Kubernetes, a one-off
task on ECS, or the `migrate` service in `deploy/compose.yaml`.

It is idempotent, serialises on an advisory lock, and re-runs every entry on every call. The
application refuses to start if it can see `DATABASE_OWNER_URL`, so a single container that
migrates and then serves must drop the credential in between:
`env -u DATABASE_OWNER_URL uvicorn ...`.

**The database must have applied exactly the entries the build carries**, in both directions
([adr/0003](adr/0003-the-application-never-applies-ddl.md)). The cost, worth knowing before
your first rolling deploy: between the release step and the last old instance being replaced,
any instance of the *previous* version that restarts will not come back up. Already-running
instances are fine. If that window matters, make the migration and the rollout one step — scale
down, migrate, scale up — and plan a rollback as a schema rollback.

## Timeouts

Every wait on Postgres has a bound, and `Timeouts` in `app/store/pg.py` sets each one:

- **A statement** that runs too long is cancelled by Postgres, which raises `QueryCanceledError`.
- **A server that does not answer** at all makes asyncpg raise `TimeoutError`.
- **A transaction left open** with nothing sent is ended by Postgres. The pool replaces the
  connection.
- **A request that finds every connection in use** waits, then raises `PoolExhausted`.

Each one answers `500` and writes its traceback to the log. Your platform's request timeout does
not replace them. It closes the client's connection and leaves the handler running with a
connection in hand, so a database that stops answering fills the pool.

To change a bound, pass a different `Timeouts` to `PostgresDatabase` in `app/wiring.py`. It
applies to every route.

## Logs

The backend writes one JSON object per line to stdout, and nothing else. Every cloud's container
runtime collects stdout with no agent and no SDK, so there is nothing to configure in the
application. The names are the ones Cloud Logging reads as they are, and the HTTP fields follow
the OpenTelemetry semantic conventions.
[adr/0009](adr/0009-every-log-line-is-declared-and-written-as-json-to-stdout.md) says why every
line is declared.

| Field | What it holds |
|---|---|
| `time` | ISO 8601, UTC |
| `severity` | `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `message` | a constant sentence; values go in fields of their own |
| `logger` | `app` for this project's lines, a library's name for its own |
| `request_id` | while a request is served; the response carries it as `X-Request-ID` |
| `trace_id`, `span_id` | while a traced request is served; the ids its spans carry |
| `exception` | the whole traceback, when there is one |

**What each cloud does with it:**

- **GCP.** Cloud Logging parses each line into `jsonPayload` and reads `severity` and `message`.
  Error Reporting groups on `exception` at no charge.
- **AWS.** CloudWatch Logs Insights finds the fields at query time, on the Standard log class
  only. **A log group keeps its data forever until you set a retention period**, so set one.
  A data protection policy on the group masks emails, card numbers and credentials as they
  arrive. It is billed per GB scanned, and it is worth having as a second layer.
- **Azure.** Container Apps stores the line as one string. Read it with `parse_json` in KQL.

**Retention is yours to set, and shorter is safer.** The log holds personal data even when
nobody meant it to: an exception's message is written as the library wrote it. 14 to 30 days
suits operational logs. When you add security events, PCI DSS asks for 12 months.

**Browser failures arrive here too**, as lines whose `message` is `client event` and whose
`source` is `client`. Anybody can post one, so read them as a report and never as evidence.

## Traces and metrics

Off until `OTEL_EXPORTER_OTLP_ENDPOINT` names a Collector, which the application posts to over
OTLP/HTTP. [adr/0010](adr/0010-traces-and-metrics-leave-over-otlp-to-a-collector-the-deployment-owns.md)
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
labels such as `deployment.environment`, and its own `OTEL_EXPORTER_OTLP_HEADERS`, `_TIMEOUT` and
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
are waiting for a connection, and `PoolExhausted` in the log follows. Alert on
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
level and nothing else. [adr/0008](adr/0008-a-route-cannot-escape-the-identity-seam.md) says
what a replacement owes.
