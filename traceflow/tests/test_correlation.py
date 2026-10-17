"""Trace Correlation: rebuilds the call tree from parent/child links."""
import pytest

from app.correlation import correlate
from helpers import TRACE_ID, checkout_trace


def test_complete_trace_builds_call_tree():
    trace = correlate(TRACE_ID, checkout_trace())
    assert trace.is_complete
    assert trace.root_span.service_name == "user-service"
    assert trace.root_span.kind == "SERVER"
    assert trace.service_path == ["user-service", "order-service", "payment-service"]
    depths = {node.span.span_id: node.depth for node in trace.iter_nodes()}
    assert depths == {"u1": 0, "u2": 1, "o1": 2, "o2": 3, "p1": 4}


def test_span_arrival_order_does_not_matter():
    trace = correlate(TRACE_ID, list(reversed(checkout_trace())))
    assert trace.is_complete
    assert trace.service_path == ["user-service", "order-service", "payment-service"]


def test_missing_parent_marks_trace_incomplete():
    spans = [s for s in checkout_trace() if s.span_id != "o1"]   # order SERVER span lost
    trace = correlate(TRACE_ID, spans)
    assert not trace.is_complete
    assert trace.missing_parent_ids == ["o1"]
    assert len(trace.roots) == 2


def test_total_duration_covers_whole_request():
    trace = correlate(TRACE_ID, checkout_trace(payment_ms=100))
    assert trace.duration_ms == pytest.approx(140)