"""
Trace Buffer - holds spans until a trace is considered complete.

Spans of one trace arrive at different times from different services
(each service exports in its own batches). The buffer groups them by trace_id
and releases a trace only after no new span has arrived for
`idle_timeout_seconds`. Only then can tail-based sampling see the whole trace.
"""
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Set

from app.models import Span

logger = logging.getLogger("traceflow.buffer")


@dataclass
class BufferedTrace:
    trace_id: str
    first_seen: float
    last_seen: float
    spans: List[Span] = field(default_factory=list)
    span_ids: Set[str] = field(default_factory=set)
    forced: bool = False  # True if released early because the buffer was full

    @property
    def services(self) -> List[str]:
        return sorted({span.service_name for span in self.spans})


class TraceBuffer:
    def __init__(
        self,
        idle_timeout_seconds: float,
        max_traces: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_traces < 1:
            raise ValueError("max_traces must be at least 1")
        self.idle_timeout_seconds = idle_timeout_seconds
        self.max_traces = max_traces
        self._clock = clock  # injectable so tests can control time
        self._traces: "OrderedDict[str, BufferedTrace]" = OrderedDict()
        self._overflow: List[BufferedTrace] = []
        self._lock = threading.Lock()

    def add_spans(self, spans: List[Span]) -> int:
        """Add spans to their traces. Returns how many were new (duplicates ignored)."""
        now = self._clock()
        added = 0
        with self._lock:
            for span in spans:
                trace = self._traces.get(span.trace_id)
                if trace is None:
                    trace = BufferedTrace(trace_id=span.trace_id, first_seen=now, last_seen=now)
                    self._traces[span.trace_id] = trace
                    self._evict_if_full()
                if span.span_id in trace.span_ids:
                    continue  # e.g. the Collector retried a request
                trace.spans.append(span)
                trace.span_ids.add(span.span_id)
                trace.last_seen = now
                added += 1
        return added

    def _evict_if_full(self) -> None:
        # Never silently lose data: the oldest trace is released early instead.
        while len(self._traces) > self.max_traces:
            _, oldest = self._traces.popitem(last=False)
            oldest.forced = True
            self._overflow.append(oldest)
            logger.warning("Buffer full - releasing trace early trace_id=%s", oldest.trace_id)

    def pop_ready(self) -> List[BufferedTrace]:
        """Remove and return every trace that has been idle long enough."""
        now = self._clock()
        with self._lock:
            ready = self._overflow
            self._overflow = []
            idle_ids = [
                trace_id
                for trace_id, trace in self._traces.items()
                if now - trace.last_seen >= self.idle_timeout_seconds
            ]
            for trace_id in idle_ids:
                ready.append(self._traces.pop(trace_id))
        return ready

    def snapshot(self) -> List[Dict]:
        """Current buffer contents (for the /api/buffer endpoint)."""
        now = self._clock()
        with self._lock:
            return [
                {
                    "trace_id": t.trace_id,
                    "span_count": len(t.spans),
                    "services": t.services,
                    "age_seconds": round(now - t.first_seen, 1),
                    "idle_seconds": round(now - t.last_seen, 1),
                }
                for t in self._traces.values()
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._traces)