"""
OpenTelemetry setup for a TraceFlow microservice.
(The same file is copied into every service so each container is self-contained.)

TRACE_EXPORTER:
  console -> print one line per finished span (local debugging, Stage 6)
  otlp    -> send spans via OTLP/HTTP to the OpenTelemetry Collector (Stage 7+)
  none    -> tracing enabled but spans are not exported
"""
import logging
import os

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] [trace_id=%(trace_id)s] %(message)s"


def _format_span(span) -> str:
    """Compact one-line representation of a finished span."""
    ctx = span.get_span_context()
    parent = format(span.parent.span_id, "016x") if span.parent else "ROOT"
    duration_ms = (span.end_time - span.start_time) / 1_000_000
    service = span.resource.attributes.get("service.name", "unknown")
    attrs = span.attributes or {}
    http_status = attrs.get("http.response.status_code", attrs.get("http.status_code", "-"))
    return (
        f"[SPAN] trace_id={format(ctx.trace_id, '032x')} "
        f"span_id={format(ctx.span_id, '016x')} parent={parent} "
        f"service={service} kind={span.kind.name} name=\"{span.name}\" "
        f"http_status={http_status} duration={duration_ms:.1f}ms "
        f"status={span.status.status_code.name}\n"
    )


class TraceContextFilter(logging.Filter):
    """Adds the current trace_id to every log record (log <-> trace correlation)."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        return True


def setup_telemetry(app, service_name: str) -> None:
    exporter_type = os.getenv("TRACE_EXPORTER", "console").lower()

    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("DEPLOYMENT_ENV", "local"),
    })
    provider = TracerProvider(resource=resource)

    if exporter_type == "console":
        provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter(formatter=_format_span))
        )
    elif exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces"
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    elif exporter_type != "none":
        raise ValueError(f"Unknown TRACE_EXPORTER '{exporter_type}' (use console, otlp or none)")

    trace.set_tracer_provider(provider)

    # SERVER spans for incoming requests; /health is excluded to avoid noise
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="health",
        exclude_spans=["receive", "send"],
    )
    # CLIENT spans for outgoing httpx calls + W3C traceparent header injection
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)

    # Put trace_id into this service's log lines
    for handler in logging.getLogger().handlers:
        handler.addFilter(TraceContextFilter())
        handler.setFormatter(logging.Formatter(LOG_FORMAT))

    logger.info("OpenTelemetry enabled service=%s exporter=%s", service_name, exporter_type)