"""Trace Buffer: groups spans by trace_id and waits until a trace is complete."""
from app.buffer import TraceBuffer
from helpers import checkout_trace

A = "a" * 32
B = "b" * 32


class FakeClock:
    """Lets the test control time instead of really waiting."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_spans_are_grouped_by_trace_id():
    buffer = TraceBuffer(idle_timeout_seconds=10, max_traces=100, clock=FakeClock())
    buffer.add_spans(checkout_trace(A))
    buffer.add_spans(checkout_trace(B))
    assert len(buffer) == 2
    counts = {t["trace_id"]: t["span_count"] for t in buffer.snapshot()}
    assert counts == {A: 5, B: 5}


def test_trace_released_only_after_idle_timeout():
    clock = FakeClock()
    buffer = TraceBuffer(idle_timeout_seconds=10, max_traces=100, clock=clock)
    buffer.add_spans(checkout_trace(A))

    clock.now = 9.9
    assert buffer.pop_ready() == []

    clock.now = 10.0
    ready = buffer.pop_ready()
    assert [t.trace_id for t in ready] == [A]
    assert len(ready[0].spans) == 5
    assert len(buffer) == 0


def test_late_spans_reset_the_idle_timer():
    clock = FakeClock()
    buffer = TraceBuffer(idle_timeout_seconds=10, max_traces=100, clock=clock)
    spans = checkout_trace(A)
    buffer.add_spans(spans[:3])      # first batch (e.g. from payment + order)
    clock.now = 8
    buffer.add_spans(spans[3:])      # second batch arrives later (e.g. from user)

    clock.now = 12                   # only 4 s since the last span
    assert buffer.pop_ready() == []

    clock.now = 18
    ready = buffer.pop_ready()
    assert len(ready[0].spans) == 5


def test_duplicate_spans_are_ignored():
    buffer = TraceBuffer(idle_timeout_seconds=10, max_traces=100, clock=FakeClock())
    spans = checkout_trace(A)
    assert buffer.add_spans(spans) == 5
    assert buffer.add_spans(spans) == 0   # e.g. the Collector retried a request


def test_full_buffer_releases_oldest_trace_early():
    buffer = TraceBuffer(idle_timeout_seconds=10, max_traces=1, clock=FakeClock())
    buffer.add_spans(checkout_trace(A))
    buffer.add_spans(checkout_trace(B))
    ready = buffer.pop_ready()
    assert [t.trace_id for t in ready] == [A]
    assert ready[0].forced is True
    assert len(buffer) == 1