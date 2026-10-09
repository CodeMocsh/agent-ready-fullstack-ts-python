# 0012. Traces and metrics leave over OTLP, to a Collector the deployment owns

Date: 2026-10-05

## Status

Accepted.

## Context

Every destination worth having accepts OTLP, and `docs/deployment.md` names them. The
destinations differ in authentication, temporality and endpoint. Each of those is Collector
configuration. When the application names a destination, a move to another cloud is a change to
the application.

W3C Trace Context warns about a public endpoint that continues the trace of any caller. A caller
can send it forged ids, and can make it sample everything.

## Decision

- `app/telemetry.py::instrument` instruments FastAPI and asyncpg with OpenTelemetry.
  `app/telemetry.py::otlp` exports over OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `app/wiring.py::build_telemetry` reads the settings. With no endpoint it returns `None`, and
  `create_app` instruments nothing. It refuses to start on a variable that this process would
  not act on. A variable a deployment sets then either takes effect or stops the start.
  `tests/wiring/test_wiring.py` holds each refusal.
- The application never names a vendor or a cloud. A Collector beside the application decides
  where the data goes, and the deployment owns that Collector.
- A span keeps only `SPAN_ATTRIBUTES`, and a metric keeps only `METRIC_ATTRIBUTES`, for the
  reason [0011](0011-every-log-line-is-declared-and-written-as-json-to-stdout.md) gives for log
  lines. `log.span_attribute_dropped` names a dropped span attribute once, and never its value.
  `tests/telemetry/test_telemetry.py::test_nothing_the_request_carried_leaves_in_a_span_or_a_metric`
  holds this.
- A request starts its own trace and keeps the caller's trace as a link. `_CallerAsLink` and
  `_LinkTheCaller` in `app/telemetry.py` do this.
  `test_a_caller_from_outside_starts_a_new_trace_and_is_kept_as_a_link` holds it.
- `TRUST_INBOUND_TRACE_CONTEXT=1` continues the caller's trace instead. It is for a service whose
  callers are the deployment's own. `test_a_trusted_caller_is_continued_in_the_same_trace` holds
  it.
- Baggage is never read or forwarded.
- Only a server span starts a trace. A query in a probe, or in work outside any request, exports
  no span. Work that polls Postgres then adds no trace. `_RequestsOnly` in `app/telemetry.py` does
  this. `test_a_span_outside_any_request_starts_no_trace` and
  `tests/integration/test_telemetry.py::test_a_query_exports_only_inside_a_traced_request` hold
  it.

## Considered options

- **Zero-code instrumentation (`opentelemetry-instrument`).** It configures itself from the
  environment at import. `app/wiring.py` exists to read the environment in one place and to
  refuse what the process does not act on.
- **Export straight to a cloud.** Each cloud needs its own exporter and credentials in the
  application, and the application is then bound to that cloud.
- **OpenTelemetry in the browser.** It is experimental, and it needs a `connect-src` wider than
  `'self'`.

## Consequences

- OpenTelemetry's SDK and instrumentations are in every project. The instrumentations are 0.x
  betas that move with the SDK.
- A change of destination, or a send to two destinations during a move, is a change to the
  Collector only.
- A Collector that does not answer loses spans from a bounded queue, and never blocks a request.
  Shutdown waits up to the export timeout for traces, then again for metrics.
  `test_a_collector_that_hangs_slows_no_request_and_holds_shutdown_to_two_timeouts` holds this.
- A process has one instrumented app. The propagator and the asyncpg instrumentation are
  process-wide, so `instrument` raises on a second app.
  `test_a_second_app_in_the_same_process_refuses_to_be_instrumented` holds this.
