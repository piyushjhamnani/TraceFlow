"""Latency & Error Analysis: self-time, bottleneck, root cause, status."""
import pytest

from app.analysis import analyze
from app.correlation import correlate
from helpers import TRACE_ID, checkout_trace

THRESHOLD_MS = 2000


def analyze_trace(**kwargs):
    return analyze(correlate(TRACE_ID, checkout_trace(**kwargs)), THRESHOLD_MS)


def test_normal_trace_is_success():
    a = analyze_trace(payment_ms=100)
    assert a.status == "SUCCESS"
    assert not a.is_slow and not a.is_error
    assert a.total_latency_ms == pytest.approx(140)


def test_slow_trace_identifies_payment_as_bottleneck():
    a = analyze_trace(payment_ms=5000)
    assert a.status == "SLOW"
    assert a.bottleneck_service == "payment-service"
    assert a.bottleneck_self_time_ms == pytest.approx(5000)


def test_self_time_excludes_waiting_for_downstream():
    a = analyze_trace(payment_ms=5000)
    services = {s.service_name: s for s in a.services}
    # User's span lasts ~5 s, but almost all of that is waiting for Order
    assert services["user-service"].duration_ms == pytest.approx(5040)
    assert services["user-service"].self_time_ms == pytest.approx(10)
    assert services["order-service"].self_time_ms == pytest.approx(10)
    assert services["payment-service"].self_time_ms == pytest.approx(5000)
    assert a.network_ms == pytest.approx(20)


def test_root_cause_is_the_deepest_failed_span():
    a = analyze_trace(fail=True)
    assert a.status == "ERROR"
    assert a.error_span_count == 5                # error propagated to every span
    assert a.root_cause.service_name == "payment-service"
    assert a.root_cause.span_name == "POST /payments"
    assert a.root_cause.http_status_code == 500


def test_latency_threshold_is_configurable():
    trace = correlate(TRACE_ID, checkout_trace(payment_ms=1500))   # total 1540 ms
    assert analyze(trace, latency_threshold_ms=2000).is_slow is False
    assert analyze(trace, latency_threshold_ms=1000).is_slow is True