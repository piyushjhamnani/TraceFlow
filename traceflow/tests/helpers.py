"""Builds realistic test data: a checkout trace (User -> Order -> Payment)
and a real OTLP protobuf request. No network or running services needed."""
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Status
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan

from app.models import Span

MS = 1_000_000
T0 = 1_700_000_000_000_000_000
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def make_span(trace_id, span_id, parent, service, kind, name, start_ms, duration_ms,
              status="UNSET", http=None):
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        kind=kind,
        service_name=service,
        start_time_ns=T0 + int(start_ms * MS),
        end_time_ns=T0 + int((start_ms + duration_ms) * MS),
        status_code=status,
        http_status_code=http,
    )


def checkout_trace(trace_id=TRACE_ID, payment_ms=100.0, fail=False, start_ms=0.0):
    """User -> Order -> Payment, like a real checkout.

    Every span starts 5 ms after its parent and ends 5 ms before it, so:
      user-service / order-service own work = 10 ms each,
      each CLIENT span has 10 ms of network time (20 ms total),
      payment-service works `payment_ms`,
      total latency = payment_ms + 40 ms.
    """
    p = payment_ms
    s = start_ms
    up = "ERROR" if fail else "UNSET"
    return [
        make_span(trace_id, "u1", None, "user-service", "SERVER", "POST /users/{user_id}/checkout",
                  s + 0, p + 40, up, 502 if fail else 200),
        make_span(trace_id, "u2", "u1", "user-service", "CLIENT", "POST",
                  s + 5, p + 30, up, 502 if fail else 201),
        make_span(trace_id, "o1", "u2", "order-service", "SERVER", "POST /orders",
                  s + 10, p + 20, up, 502 if fail else 201),
        make_span(trace_id, "o2", "o1", "order-service", "CLIENT", "POST",
                  s + 15, p + 10, up, 500 if fail else 201),
        make_span(trace_id, "p1", "o2", "payment-service", "SERVER", "POST /payments",
                  s + 20, p, up, 500 if fail else 201),
    ]


def _kv(key, **value):
    return KeyValue(key=key, value=AnyValue(**value))


def otlp_request_bytes(trace_id=TRACE_ID):
    """A real OTLP ExportTraceServiceRequest (protobuf) with two spans."""
    root = PbSpan(
        trace_id=bytes.fromhex(trace_id),
        span_id=bytes.fromhex("1111111111111111"),
        name="POST /payments",
        kind=PbSpan.SPAN_KIND_SERVER,
        start_time_unix_nano=T0,
        end_time_unix_nano=T0 + 5 * MS,
        attributes=[_kv("http.response.status_code", int_value=201)],   # new semantic convention
    )
    child = PbSpan(
        trace_id=bytes.fromhex(trace_id),
        span_id=bytes.fromhex("2222222222222222"),
        parent_span_id=bytes.fromhex("1111111111111111"),
        name="charge card",
        kind=PbSpan.SPAN_KIND_CLIENT,
        start_time_unix_nano=T0 + 1 * MS,
        end_time_unix_nano=T0 + 3 * MS,
        status=Status(code=Status.STATUS_CODE_ERROR, message="boom"),
        attributes=[_kv("http.status_code", int_value=500)],             # old semantic convention
    )
    request = ExportTraceServiceRequest(resource_spans=[
        ResourceSpans(
            resource=Resource(attributes=[_kv("service.name", string_value="payment-service")]),
            scope_spans=[ScopeSpans(spans=[root, child])],
        )
    ])
    return request.SerializeToString()