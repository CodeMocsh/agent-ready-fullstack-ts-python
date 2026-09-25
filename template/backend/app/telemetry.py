"""Traces and metrics, sent over OTLP to the Collector a deployment names.

Nothing here runs unless `wiring.build_telemetry` returns settings. When it does, every request
is a server span named by its route template, every query a database span, and every request
adds to `http.server.request.duration`; `/health` and `/ready` are left out. A pooled substrate
reports its connections as `db.client.connection.count`, by state, and as
`db.client.connection.max`. This module instruments and `app.log` reads the current span; ruff
refuses `opentelemetry` anywhere else.

**What leaves the process is declared here.** A span keeps only the attributes in
`SPAN_ATTRIBUTES`, its status code, none of its events and no caller's `tracestate`; a
metric keeps only `METRIC_ATTRIBUTES` and carries no exemplars. A span attribute left out is reported once, by
name. A caller's trace context
is continued only when the deployment trusts its callers, and is otherwise a link on a new
trace. `docs/adr/0010` holds the reasoning.
"""

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, final, override

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.context import Context, create_key, get_value, set_value
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, SpanContext, Status, TraceState
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app import log
from app.store import Database, Pooled
from app.wiring import SEMCONV_ENV, STABLE_SEMCONV, TelemetrySettings

SPAN_ATTRIBUTES: Final = frozenset(
    {
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "error.type",
        "url.scheme",
        "network.protocol.version",
        "db.system.name",
        "db.namespace",
        "db.operation.name",
        "db.collection.name",
    }
)
"""The span attributes that may leave the process. Never a path, a query string, a header, an
address, a user agent or query text."""

METRIC_ATTRIBUTES: Final = frozenset(
    {
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "error.type",
        "db.client.connection.state",
        "db.client.connection.pool.name",
    }
)
"""The metric attributes that may leave the process. Never a tenant or a user."""

PROBES: Final = r"^https?://[^/]+(/api)?/(health|ready)$"
"""The URLs never traced or measured: the two probes, bare or under `app.serve`'s prefix."""

_CALLER: Final = create_key("untrusted-caller")


@dataclass(frozen=True)
class Instruments:
    """What `instrument` installed, so the lifespan can stop it."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    semconv_before: str | None

    def observe(self, database: Database) -> None:
        """Report the connections of `database` at every collection. A substrate with no pool
        has no connections to count, and is not observed."""
        if not isinstance(database, Pooled):
            return

        def count(_options: CallbackOptions) -> Iterable[Observation]:
            now = database.connections()
            pool = {"db.client.connection.pool.name": now.pool}
            return [
                Observation(now.used, {**pool, "db.client.connection.state": "used"}),
                Observation(now.idle, {**pool, "db.client.connection.state": "idle"}),
            ]

        def most(_options: CallbackOptions) -> Iterable[Observation]:
            now = database.connections()
            return [Observation(now.max_size, {"db.client.connection.pool.name": now.pool})]

        meter = self.meter_provider.get_meter(__name__)
        meter.create_observable_up_down_counter(
            "db.client.connection.count", callbacks=[count], unit="{connection}"
        )
        meter.create_observable_up_down_counter(
            "db.client.connection.max", callbacks=[most], unit="{connection}"
        )

    def shutdown(self) -> None:
        """Flush and stop both providers, take the instrumentation off asyncpg, and put back
        `SEMCONV_ENV`. Waits up to the export timeout for each provider, one after the other,
        when the Collector does not answer."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        AsyncPGInstrumentor().uninstrument()
        if self.semconv_before is None:
            os.environ.pop(SEMCONV_ENV, None)
        else:
            os.environ[SEMCONV_ENV] = self.semconv_before


def otlp(settings: TelemetrySettings) -> tuple[SpanExporter, MetricReader]:
    """Exporters that post to the Collector over OTLP/HTTP. Headers, timeouts and certificates
    are the SDK's own `OTEL_EXPORTER_OTLP_*` variables, which it reads itself."""
    return (
        OTLPSpanExporter(endpoint=f"{settings.endpoint}/v1/traces"),
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{settings.endpoint}/v1/metrics")
        ),
    )


def instrument(
    app: FastAPI, settings: TelemetrySettings, spans: SpanExporter, metrics: MetricReader
) -> Instruments:
    """Instrument `app` and asyncpg, exporting through `spans` and `metrics`.

    One instrumented app per process: the propagator and the asyncpg instrumentation are
    process-wide, so a second call before `Instruments.shutdown` raises. Sets `SEMCONV_ENV` to
    `STABLE_SEMCONV` until then.
    """
    if AsyncPGInstrumentor().is_instrumented_by_opentelemetry:
        raise RuntimeError(
            "asyncpg is already instrumented by another app in this process. Shut that app's "
            "Instruments down first."
        )
    semconv_before = os.environ.get(SEMCONV_ENV)
    os.environ[SEMCONV_ENV] = STABLE_SEMCONV
    set_global_textmap(
        TraceContextTextMapPropagator() if settings.trust_inbound_context else _CallerAsLink()
    )
    resource = Resource.create({"service.name": settings.service})
    tracer_provider = TracerProvider(
        sampler=ParentBased(TraceIdRatioBased(settings.sampling_ratio)), resource=resource
    )
    tracer_provider.add_span_processor(_LinkTheCaller())
    tracer_provider.add_span_processor(BatchSpanProcessor(_Declared(spans)))
    meter_provider = MeterProvider(
        metric_readers=[metrics],
        resource=resource,
        exemplar_filter=AlwaysOffExemplarFilter(),
        views=[View(instrument_name="*", attribute_keys=set(METRIC_ATTRIBUTES))],
    )
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        exclude_spans=["receive", "send"],
        excluded_urls=PROBES,
    )
    AsyncPGInstrumentor().instrument(tracer_provider=tracer_provider)
    return Instruments(tracer_provider, meter_provider, semconv_before)


class _CallerAsLink(TextMapPropagator):
    """Reads a caller's `traceparent` and keeps it aside rather than continuing it, so the
    request starts a trace of its own. Baggage is never read."""

    _w3c: Final = TraceContextTextMapPropagator()

    @override
    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        base = Context() if context is None else context
        caller = trace.get_current_span(self._w3c.extract(carrier, getter=getter))
        if not caller.get_span_context().is_valid:
            return base
        return set_value(_CALLER, caller.get_span_context(), base)

    @override
    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        self._w3c.inject(carrier, context=context, setter=setter)

    @property
    @override
    def fields(self) -> set[str]:
        return self._w3c.fields


class _LinkTheCaller(SpanProcessor):
    """Links the root span of a request to the caller `_CallerAsLink` kept aside."""

    @override
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        caller = get_value(_CALLER, parent_context)
        if span.parent is None and isinstance(caller, SpanContext):
            span.add_link(caller)


@final
class _Declared(SpanExporter):
    """Hands the exporter each span with only its declared attributes, its status code, no
    events, links without attributes, and no `tracestate`. Reports each attribute it leaves
    out, once."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter
        self._reported: set[str] = set()

    @override
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export([self._declared(one) for one in spans])

    @override
    def shutdown(self) -> None:
        self._exporter.shutdown()

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)

    def _declared(self, span: ReadableSpan) -> ReadableSpan:
        attributes = dict(span.attributes) if span.attributes is not None else {}
        for key in attributes.keys() - SPAN_ATTRIBUTES - self._reported:
            self._reported.add(key)
            log.span_attribute_dropped(key)
        return ReadableSpan(
            name=span.name,
            context=None if span.context is None else _without_state(span.context),
            parent=None if span.parent is None else _without_state(span.parent),
            resource=span.resource,
            attributes={k: v for k, v in attributes.items() if k in SPAN_ATTRIBUTES},
            events=(),
            links=tuple(Link(_without_state(one.context)) for one in span.links),
            kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )


def _without_state(context: SpanContext) -> SpanContext:
    return SpanContext(
        trace_id=context.trace_id,
        span_id=context.span_id,
        is_remote=context.is_remote,
        trace_flags=context.trace_flags,
        trace_state=TraceState(),
    )
