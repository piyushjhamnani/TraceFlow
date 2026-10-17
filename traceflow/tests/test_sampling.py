"""Tail-Based Sampling: KEEP / DROP decisions and their reasons."""
import hashlib

import pytest

from app.analysis import analyze
from app.correlation import correlate
from app.sampling import SamplingPolicy, decide, in_baseline_sample
from helpers import TRACE_ID, checkout_trace


def decision_for(policy=SamplingPolicy(), spans=None, **kwargs):
    trace = correlate(TRACE_ID, spans if spans is not None else checkout_trace(**kwargs))
    return decide(analyze(trace, 2000), trace.is_complete, policy)


def test_normal_trace_is_dropped():
    d = decision_for(payment_ms=100)
    assert (d.decision, d.reason) == ("DROP", "NORMAL_TRACE")


def test_slow_trace_is_kept_for_high_latency():
    d = decision_for(payment_ms=5000)
    assert (d.decision, d.reason) == ("KEEP", "HIGH_LATENCY")


def test_error_trace_is_kept_for_error():
    d = decision_for(fail=True)
    assert (d.decision, d.reason) == ("KEEP", "ERROR")


def test_slow_and_failed_trace_reports_both_rules():
    d = decision_for(payment_ms=5000, fail=True)
    assert d.reason == "ERROR"                      # error has the highest priority
    assert d.matched_rules == ["ERROR", "HIGH_LATENCY"]


def test_incomplete_trace_handling_is_configurable():
    spans = [s for s in checkout_trace() if s.span_id != "o1"]
    keep = decision_for(SamplingPolicy(keep_incomplete_traces=True), spans=spans)
    assert (keep.decision, keep.reason) == ("KEEP", "INCOMPLETE_TRACE")
    drop = decision_for(SamplingPolicy(keep_incomplete_traces=False), spans=spans)
    assert drop.decision == "DROP"


def test_full_baseline_keeps_healthy_traces():
    d = decision_for(SamplingPolicy(baseline_sample_rate=1.0), payment_ms=100)
    assert (d.decision, d.reason) == ("KEEP", "BASELINE_SAMPLE")


def test_baseline_sampling_is_consistent_and_proportional():
    assert in_baseline_sample(TRACE_ID, 0.0) is False
    assert in_baseline_sample(TRACE_ID, 1.0) is True
    # the same trace always gets the same answer
    assert in_baseline_sample(TRACE_ID, 0.5) == in_baseline_sample(TRACE_ID, 0.5)
    # about 25% of random trace IDs are kept at rate 0.25
    ids = [hashlib.md5(str(i).encode()).hexdigest() for i in range(2000)]
    share = sum(in_baseline_sample(trace_id, 0.25) for trace_id in ids) / len(ids)
    assert 0.20 < share < 0.30


def test_invalid_baseline_rate_is_rejected():
    with pytest.raises(ValueError):
        SamplingPolicy(baseline_sample_rate=1.5)