# 0011. Every log line is declared, and written as JSON to stdout

Date: 2026-10-05

## Status

Accepted.

## Context

Secrets reach a log through an ordinary code path that writes a value nobody meant to log.
Twitter and GitHub in 2018, Robinhood in 2019, and the Meta passwords the Irish DPC fined in 2024
are all this case. A scrubber catches the shapes it knows, and it fails silently on the next
shape. A field that does not exist cannot carry a value.

Libraries log too. httpx writes every URL it requests at `INFO`, and a URL carries its query
string. uvicorn's access log writes the path with its query string.

The browser sees failures that the backend never sees. A vendor SDK sends them to a third party.
That adds an origin to `connect-src`, and it usually puts an identifier or storage in the
browser.

Without a tenant on each request line, one tenant's requests cannot be read apart from another's
on the log alone. A contextvar that a dependency sets inside the request does not reach the
middleware that writes the line after the response.

## Decision

- `app/log.py` is the only module that writes the log. Ruff's `TID251` refuses an import of
  `logging` or `structlog` through `banned-api` in `backend/pyproject.toml.jinja`, and `T20`
  refuses `print`.
- Each log line is a function in `app/log.py`. Its parameters are the only fields the line
  carries.
- Each line is one JSON object on stdout. The field names are the ones Cloud Logging, CloudWatch
  and Azure Monitor parse without an agent. The HTTP fields follow the OpenTelemetry semantic
  conventions. `tests/log/test_log.py` holds the format.
- A library record goes through the same formatter, and only at `WARNING` and above.
  `test_a_library_says_nothing_below_a_warning` holds this. uvicorn's access log is off.
  `request completed` replaces it and names the route template, never the path.
  `test_nothing_the_request_carried_reaches_the_log` sends a canary in each place a request
  carries a value, and fails if the canary reaches the log.
- `request completed` names the tenant. The tenant requirement's router carries
  `app/deps.py::resolved_tenant`, which depends on `tenant_for` and calls
  `app/log.py::name_on_request_line`. That function writes to `request.state`. `request.state` is
  the one place where a dependency can leave a value that the middleware reads after the
  response. `tenant_id` is null when the request resolves no tenant: on a public route, or when
  the identity seam refuses the request.
  `test_each_request_is_logged_with_the_tenant_it_resolved` holds this.
- A project declares more fields on `request completed` in `FIELDS` in `app/request_line.py`,
  and names each one with `name_on_request_line`. A field is an id such as `user.id`, never a
  name, an email or text that a person typed. A field nobody declared raises
  `UndeclaredRequestField` and does not reach the log.
  `test_a_field_nobody_declared_is_refused_rather_than_logged` holds this.
- Every line carries `service.version`. `app/wiring.py::build_service_version` reads it from
  `OTEL_RESOURCE_ATTRIBUTES`, which the OpenTelemetry SDK reads for spans. A span and a line then
  name the same build. The field is null when the deployment names no version.
- The browser reports a failure from `frontend/src/client-events.ts` to `POST /client-events`,
  which `app/routes/public.py::record_client_events` serves. `log.client_event` writes it with
  `source=client`. The route is public, so `ClientEvent` in `app/models/client_events.py` is the
  whole defence. Each field is an enum, a bounded identifier or a number, and it forbids any
  other field. `tests/routes/test_client_events.py` holds this.
- A first-party route keeps `connect-src 'self'` in `app/serve.py`. It sends nothing to a third
  party and needs no identifier or storage in the browser. That is the strongest position under
  the ePrivacy Directive (EDPB Guidelines 2/2023).

## Considered options

- **A vendor SDK such as Sentry.** It adds a third party to `connect-src`. Sentry's JavaScript
  SDK 11 collects cookies, headers and bodies unless each one is turned off.
- **The OpenTelemetry logs SDK.** It is experimental on both halves. The field names follow its
  conventions, so a move to it is a configuration change.
- **Scrubbing by key name or pattern as the main control.** It is silent when it misses. It is a
  second layer in the pipeline, not the first.

## Consequences

- A new log line is a new function in `app/log.py`. A new field on `request completed` is a new
  name in `app/request_line.py`.
- An exception's message is written as the library wrote it. A Postgres unique violation names
  the conflicting value.
- Anybody can post a client event. A client event is a report, and never evidence of what the
  server did.
- Logs leave the process only on stdout. There is no security-event stream, no audit log, no
  keyed hashing of identifiers, and no OTLP export of logs.
