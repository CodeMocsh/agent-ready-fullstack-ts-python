# Traces and metrics leave over OTLP, to a Collector the deployment owns

`app/telemetry.py` instruments FastAPI and asyncpg with OpenTelemetry and exports over OTLP/HTTP
to the endpoint in `OTEL_EXPORTER_OTLP_ENDPOINT`. With no endpoint nothing is instrumented. The
application never names a vendor or a cloud: an OpenTelemetry Collector beside it decides where
the data goes, and that Collector belongs to the deployment.

## Why OTLP to a Collector

Every destination worth having accepts OTLP (`docs/deployment.md` names them). What differs
between them is authentication, temporality and endpoint, and each of those is Collector
configuration. Keeping
them there means changing clouds never touches the application, and one Collector can send to
two destinations during a move.

A span keeps only `SPAN_ATTRIBUTES`, and a metric only `METRIC_ATTRIBUTES`, for the reason
`docs/adr/0009` gives for log lines.

## Why a caller's trace is a link unless trusted

W3C Trace Context warns that a public endpoint which continues any caller's trace can be handed
forged ids, and made to sample everything. A request therefore starts its own trace and keeps
the caller's as a link. `TRUST_INBOUND_TRACE_CONTEXT=1` continues it instead, for a service
whose callers are the deployment's own. Baggage is never read or forwarded.

## Considered options

- **Zero-code instrumentation (`opentelemetry-instrument`).** It configures itself from the
  environment at import, which `app/wiring.py` exists to prevent.
- **Exporting straight to a cloud.** Each needs its own exporter and credentials in the
  application, and ties it to that cloud.
- **OpenTelemetry in the browser.** Still experimental, and it would need `connect-src` opened.

## Consequences

- OpenTelemetry's SDK and instrumentations are in every project. The instrumentations are 0.x
  betas that move with the SDK.
- A Collector that cannot be reached loses spans from a bounded queue and never blocks a
  request. Shutdown waits up to the export timeout for traces, then again for metrics.
- One instrumented app per process: the propagator and the asyncpg instrumentation are
  process-wide.
