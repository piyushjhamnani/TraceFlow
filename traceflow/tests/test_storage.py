"""SQLite storage: what is stored, counters, retention and persistence."""
import pytest

from app.analysis import analyze
from app.correlation import correlate
from app.sampling import SamplingPolicy, decide
from app.storage import TraceStore
from helpers import checkout_trace

NORMAL, SLOW, FAILED = "a" * 32, "b" * 32, "c" * 32


def record(store, trace_id, **kwargs):
    trace = correlate(trace_id, checkout_trace(trace_id, **kwargs))
    analysis = analyze(trace, 2000)
    store.record(trace, analysis, decide(analysis, trace.is_complete, SamplingPolicy()))


@pytest.fixture
def store(tmp_path):
    return TraceStore(str(tmp_path / "test.db"))


def record_three_scenarios(store):
    record(store, NORMAL, payment_ms=100)
    record(store, SLOW, payment_ms=5000)
    record(store, FAILED, fail=True)


def test_only_kept_traces_are_stored(store):
    record_three_scenarios(store)
    assert {t["trace_id"] for t in store.list_traces()} == {SLOW, FAILED}
    assert store.get_trace(NORMAL) is None       # dropped by sampling


def test_summary_counts_all_traces(store):
    record_three_scenarios(store)
    s = store.summary()
    assert s["total_traces"] == 3
    assert s["retained_traces"] == 2
    assert s["dropped_traces"] == 1
    assert s["error_traces"] == 1
    assert s["slow_traces"] == 1
    assert s["trace_reduction_percent"] == pytest.approx(33.3)
    assert s["stored_by_reason"] == {"HIGH_LATENCY": 1, "ERROR": 1}


def test_trace_detail_has_spans_in_call_order(store):
    record(store, FAILED, fail=True)
    trace = store.get_trace(FAILED)
    assert [s["span_id"] for s in trace["spans"]] == ["u1", "u2", "o1", "o2", "p1"]
    assert [s["depth"] for s in trace["spans"]] == [0, 1, 2, 3, 4]
    assert trace["reason"] == "ERROR"
    assert trace["root_cause_service"] == "payment-service"


def test_service_metrics_include_dropped_traces(store):
    record(store, NORMAL, payment_ms=100)
    record(store, FAILED, fail=True)
    metrics = {m["service_name"]: m for m in store.service_metrics()}
    assert metrics["payment-service"]["requests"] == 2
    assert metrics["payment-service"]["errors"] == 1
    assert metrics["payment-service"]["root_cause_count"] == 1
    assert metrics["user-service"]["errors"] == 1          # error propagated...
    assert metrics["user-service"]["root_cause_count"] == 0  # ...but did not start there


def test_status_filter(store):
    record_three_scenarios(store)
    assert [t["trace_id"] for t in store.list_traces(status="ERROR")] == [FAILED]


def test_retention_deletes_oldest_traces(tmp_path):
    store = TraceStore(str(tmp_path / "test.db"), max_stored_traces=2)
    for i, trace_id in enumerate(["d" * 32, "e" * 32, "f" * 32]):
        record(store, trace_id, fail=True, start_ms=i * 1000)
    assert {t["trace_id"] for t in store.list_traces()} == {"e" * 32, "f" * 32}


def test_data_survives_restart(tmp_path):
    path = str(tmp_path / "test.db")
    record(TraceStore(path), FAILED, fail=True)
    reopened = TraceStore(path)                  # like restarting TraceFlow
    assert reopened.summary()["total_traces"] == 1
    assert reopened.get_trace(FAILED) is not None


def test_reset_clears_everything(store):
    record_three_scenarios(store)
    store.reset()
    assert store.summary()["total_traces"] == 0
    assert store.list_traces() == []