# Every log line is declared, and written as JSON to stdout

`app/log.py` and `app/log_lines.py` are the only modules that may import `logging` or
`structlog`. Each line the application writes is a function in one of them, and the function's parameters are the fields the line
may carry. The browser reports a failure to `POST /client-events` as a typed event with no free
text, and the backend writes it to the same log.

## Why declared rather than scrubbed

Every well-known case of secrets in logs — Twitter and GitHub in 2018, Robinhood in 2019, the
Meta passwords the Irish DPC fined in 2024 — was an ordinary code path writing a value nobody
meant to log. A scrubber catches the shapes it knows and fails silently on the next one. A
field that does not exist cannot carry a value. ruff's `TID251` refuses any other logger, and
`T20` refuses `print`.

Libraries are not declared here, so their `INFO` is not heard — httpx writes every URL it
requests at that level, and the canary test in `tests/log/test_log.py` caught it. uvicorn's
access log is off for the same reason.

## Why the browser posts to its own backend

The Content-Security-Policy in `app/serve.py` allows `connect-src 'self'`. A first-party route
keeps it that way, sends nothing to a third party, and needs no identifier or storage in the
browser — the strongest position under the ePrivacy Directive (EDPB Guidelines 2/2023). The
route is public, so `ClientEvent` is the whole defence: every field is an enum, a bounded
identifier or a number.

## Considered options

- **A vendor SDK such as Sentry.** A third party in `connect-src`, and Sentry's JavaScript SDK 11
  collects cookies, headers and bodies unless each is turned off.
- **The OpenTelemetry logs SDK.** Still experimental on both halves. The field names follow its
  conventions, so moving later is configuration.
- **Scrubbing by key name or pattern as the main control.** Silent when it misses. It is a
  second layer in the pipeline, not a first.

## Consequences

- A new log line is a new function in `app/log_lines.py`.
- An exception's message is written as the library wrote it. A Postgres unique violation names
  the conflicting value.
- `tenant_id` is not on `request completed`: `tenant_for` is synchronous, so what it binds does
  not reach the middleware.
- Not yet: a security-event stream, an audit log, keyed hashing of identifiers, OTLP export.

## Amended 2026-10-05: the request line names its tenant and its build

The consequence above, that `tenant_id` is not on `request completed`, no longer holds. The
tenant requirement's router carries `app/deps.py::resolved_tenant`, which reaches `tenant_for`
and names the tenant through `app/log.py::name_on_request_line`. That function writes to
`request.state`, the one place a dependency can leave a value the middleware reads after the
response; a contextvar set inside the request does not reach it. One tenant's requests can now
be read apart from another's on the log alone.

A request that resolves no tenant is logged with `tenant_id` null: a public route, or a request
the identity seam refused. A route that answers `404` or `500` after the seam resolved a tenant
names that tenant.

Every line also carries `service.version`, read by `app/wiring.py::build_service_version` from
`OTEL_RESOURCE_ATTRIBUTES` -- the variable the OpenTelemetry SDK reads for the spans -- so a span
and a line name the same build. It is null when the deployment names none.

A project that resolves more than a tenant, such as a user, declares the field in
`app/request_line.py` and names it the same way. The declaration is what keeps this a declared
field: a name nobody declared raises `UndeclaredRequestField` rather than reaching the log.
