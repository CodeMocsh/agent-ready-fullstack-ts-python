# Every log line is declared, and written as JSON to stdout

The backend writes one JSON object per line to stdout. `app/log.py` is the only module that may
import `logging` or `structlog`, and each line the application writes is a function there
whose parameters are the fields the line may carry. The browser reports a failure to
`POST /client-events` as a typed event with no free text, and the backend writes it to the same
log.

## Why the log is declared rather than scrubbed

Every well-known case of secrets in logs — Twitter and GitHub in 2018, Robinhood in 2019, the
Meta passwords the Irish DPC fined in 2024 — was an ordinary code path writing a value nobody
meant to log. A scrubber that matches key names or patterns catches the shapes it knows and
fails silently on the next one. A field that does not exist cannot carry a value, so the
allowlist is the parameter list. ruff's `TID251` refuses any other logger and `T20` refuses
`print`.

Libraries log too, and those lines are not declared here. Two rules cover them:

- **Below `WARNING`, only `app` and `uvicorn.error` are heard.** httpx logs every URL it requests
  at `INFO`, with its query string. The suite caught exactly that line carrying a canary email.
- **uvicorn's access log is off.** Its record carries the path with its query string.
  `request completed` replaces it, naming the route template (`/tasks/{id}`) rather than the
  path.

`tests/log/test_log.py` sends a canary value in a body, a query string, a header, a path
parameter and a refused body, and refuses it anywhere in the output. It also counts the lines it
expected, because an empty log passes an absence check.

## Why these field names

`severity`, `message` and `time` are what Cloud Logging reads without an agent, and CloudWatch
and Azure Monitor accept them as they are. `exception` carries the whole traceback in one
field, which is where GCP's Error Reporting looks, and a multi-line traceback breaks every log
agent that reads stdout. The HTTP fields use the OpenTelemetry semantic conventions
(`http.route`, `http.response.status_code`), so a later move to OTLP renames nothing.

## Why the browser posts to its own backend

The Content-Security-Policy in `app/serve.py` allows `connect-src 'self'` and nothing else. A
first-party route keeps it that way, needs no credential in the browser, and puts the browser's
events through the same log, retention and access control as the backend's. Under the ePrivacy
Directive, JavaScript that sends device data is in scope (EDPB Guidelines 2/2023). First-party
error reporting with no identifier and no storage is the strongest case for needing no consent.

The route is public, because a page fails before sign-in as often as after, so its body is
untrusted by construction. `ClientEvent` is the whole defence. Its route is the router's
template, its error is a class name, and every field is an enum, a bounded identifier or a
number. A forged event can say nothing a real one could not.

## Considered options

**stdlib `logging` with a hand-written JSON formatter.** Rejected: structlog already does
exactly this, handles uvicorn's and asyncpg's records through `ProcessorFormatter`, and is the
common choice. The dependency cost is one package with no dependencies of its own.

**The OpenTelemetry logs SDK on both halves.** Rejected for now: its logs signal is still in
development in Python, and `@opentelemetry/sdk-logs` is experimental. The field names already
follow its conventions, so adopting it later is configuration.

**Sentry, or another vendor SDK.** Rejected as a default. It needs a third party in
`connect-src`, and Sentry's JavaScript SDK 11 collects cookies, headers and bodies unless each
is turned off. GlitchTip accepts the same SDK and self-hosts, if a project wants grouped errors.

**Redaction by key name or pattern as the main control.** Rejected: see above. It is a
reasonable second layer in the pipeline — CloudWatch data protection policies, the
OpenTelemetry Collector's `redactionprocessor` — and not a first one.

## Consequences

- A new log line is a new function in `app/log.py`. That is the point, and it is also a cost.
- An exception's message reaches `exception` as written. The application's own messages are
  under its control; a library's are not. A Postgres unique violation, for one, names the
  conflicting value.
- `tenant_id` is not on `request completed`. `tenant_for` is synchronous, so FastAPI runs it in
  a worker thread, and a value it binds does not reach the middleware that writes the line.
- Nothing rate-limits `POST /client-events` beyond its batch cap. Neither does anything
  rate-limit `POST /tasks`: limits belong at the edge in front of the process.
- Retention is the deployment's to set. `docs/deployment.md` names defaults.
- A client event that cannot be delivered is written to the browser console and not sent again.
  Sending it again would report the failure of the route that reports failures.

## Not yet

Each of these is a later decision, and none is blocked by this one:

- **A security-event stream.** Authentication success and failure, authorization failure and
  admin actions, named in the OWASP logging vocabulary and kept longer. It belongs with the
  replacement for `tenant_for`, which is the first code that has such events to write.
- **An audit log.** Who changed which record, per tenant, append-only. That is a table, not a
  log line.
- **Keyed hashing of identifiers**, for a line that must join to a user without naming one. It
  needs a key and a rotation policy.
- **OpenTelemetry traces and OTLP export.** `trace_id` and `span_id` join the lines when they
  arrive.
