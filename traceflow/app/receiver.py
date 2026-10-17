"""
OTLP receiver - converts an OTLP/HTTP protobuf export request (sent by the
OpenTelemetry Collector) into TraceFlow Span objects.
"""
from typing import Any, Dict, List, Optional

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus

from app.models import Span


def _any_value(value) -> Any:
    """Convert an OTLP AnyValue into a normal Python value."""
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind in ("string_value", "bool_value", "int_value", "double_value"):
        return getattr(value, kind)
    if kind == "bytes_value":
        return value.bytes_value.hex()
    if kind == "array_value":
        return [_any_value(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in value.kvlist_value.values}
    return str(value)


def _attributes(key_values) -> Dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in key_values}


def _enum_name(enum_type, number: int, prefix: str) -> str:
    """SPAN_KIND_SERVER -> SERVER, STATUS_CODE_ERROR -> ERROR."""
    try:
        name = enum_type.Name(number)
    except ValueError:
        return str(number)
    return name[len(prefix):] if name.startswith(prefix) else name


def _http_status(attrs: Dict[str, Any]) -> Optional[int]:
    # newer semantic conventions use http.response.status_code, older ones http.status_code
    value = attrs.get("http.response.status_code", attrs.get("http.status_code"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_otlp_traces(body: bytes) -> List[Span]:
    """Parse a protobuf ExportTraceServiceRequest. Raises DecodeError on invalid data."""
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)

    spans: List[Span] = []
    for resource_spans in request.resource_spans:
        resource_attrs = _attributes(resource_spans.resource.attributes)
        service_name = str(resource_attrs.get("service.name", "unknown-service"))

        for scope_spans in resource_spans.scope_spans:
            for pb in scope_spans.spans:
                attrs = _attributes(pb.attributes)
                spans.append(
                    Span(
                        trace_id=pb.trace_id.hex(),
                        span_id=pb.span_id.hex(),
                        parent_span_id=pb.parent_span_id.hex() or None,
                        name=pb.name,
                        kind=_enum_name(PbSpan.SpanKind, pb.kind, "SPAN_KIND_"),
                        service_name=service_name,
                        start_time_ns=pb.start_time_unix_nano,
                        end_time_ns=pb.end_time_unix_nano,
                        status_code=_enum_name(PbStatus.StatusCode, pb.status.code, "STATUS_CODE_"),
                        status_message=pb.status.message,
                        http_status_code=_http_status(attrs),
                        attributes=attrs,
                    )
                )
    return spans