"""OTLP receiver: real protobuf bytes -> TraceFlow Span objects."""
import pytest
from google.protobuf.message import DecodeError

from app.receiver import parse_otlp_traces
from helpers import TRACE_ID, otlp_request_bytes


def test_parses_otlp_protobuf_into_spans():
    spans = parse_otlp_traces(otlp_request_bytes())
    assert len(spans) == 2
    root, child = sorted(spans, key=lambda s: s.start_time_ns)

    assert root.trace_id == TRACE_ID == child.trace_id
    assert root.parent_span_id is None
    assert root.kind == "SERVER"
    assert root.service_name == "payment-service"
    assert root.http_status_code == 201          # from http.response.status_code

    assert child.parent_span_id == root.span_id
    assert child.kind == "CLIENT"
    assert child.status_code == "ERROR"
    assert child.status_message == "boom"
    assert child.http_status_code == 500         # from http.status_code
    assert child.duration_ms == pytest.approx(2.0)


def test_invalid_payload_raises_decode_error():
    with pytest.raises(DecodeError):
        parse_otlp_traces(b"\xff\xff\xff")