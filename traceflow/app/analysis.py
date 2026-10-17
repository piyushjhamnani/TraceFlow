"""
Latency & Error Analysis for one correlated trace.

Latency
  total latency   : first span start -> last span end
  service latency : time each service spent handling the request (its SERVER spans)
  self time       : time a span spent on its OWN work = its duration minus the
                    time covered by its child spans (= minus waiting for downstream)
  bottleneck      : the service with the highest self time
  network         : self time of CLIENT spans = time between "request sent" and
                    "downstream service started handling it" (+ the way back)

Errors
  error spans     : spans with status ERROR
  root cause      : the DEEPEST error span - errors propagate upwards
                    (Payment 500 -> Order 502 -> User 502), so the deepest
                    failed span is where the failure started
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.correlation import CorrelatedTrace, SpanNode

STATUS_SUCCESS = "SUCCESS"
STATUS_SLOW = "SLOW"
STATUS_ERROR = "ERROR"


@dataclass
class ServiceStats:
    service_name: str
    span_count: int = 0
    duration_ms: float = 0.0   # time spent handling requests (SERVER spans)
    self_time_ms: float = 0.0  # own work, excluding waiting for downstream services
    error_count: int = 0


@dataclass
class RootCause:
    service_name: str
    span_name: str
    kind: str
    http_status_code: Optional[int]
    status_message: str


@dataclass
class TraceAnalysis:
    trace_id: str
    root_span_name: str
    total_latency_ms: float
    latency_threshold_ms: float
    is_slow: bool
    is_error: bool
    status: str                         # SUCCESS / SLOW / ERROR
    services: List[ServiceStats]        # in call order
    bottleneck_service: Optional[str]
    bottleneck_self_time_ms: float
    network_ms: float
    error_span_count: int
    root_cause: Optional[RootCause]


def _self_time_ms(node: SpanNode) -> float:
    """Span duration minus the (merged) time covered by its children."""
    span = node.span
    intervals = sorted(
        (
            max(child.span.start_time_ns, span.start_time_ns),
            min(child.span.end_time_ns, span.end_time_ns),
        )
        for child in node.children
    )
    covered = 0
    cur_start = cur_end = None
    for start, end in intervals:
        if end <= start:
            continue
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                covered += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        covered += cur_end - cur_start

    own_ns = (span.end_time_ns - span.start_time_ns) - covered
    return max(own_ns, 0) / 1_000_000


def analyze(trace: CorrelatedTrace, latency_threshold_ms: float) -> TraceAnalysis:
    per_service: Dict[str, ServiceStats] = {}
    network_ms = 0.0
    error_nodes: List[SpanNode] = []

    for node in trace.iter_nodes():  # call order
        span = node.span
        stats = per_service.get(span.service_name)
        if stats is None:
            stats = ServiceStats(service_name=span.service_name)
            per_service[span.service_name] = stats

        stats.span_count += 1
        self_ms = _self_time_ms(node)
        if span.kind == "CLIENT":
            network_ms += self_ms          # waiting on the network / downstream
        else:
            stats.self_time_ms += self_ms  # the service's own work
        if span.kind == "SERVER":
            stats.duration_ms += span.duration_ms
        if span.is_error:
            stats.error_count += 1
            error_nodes.append(node)

    services = list(per_service.values())

    bottleneck = max(services, key=lambda s: s.self_time_ms, default=None)
    if bottleneck is not None and bottleneck.self_time_ms <= 0:
        bottleneck = None

    root_cause = None
    if error_nodes:
        # deepest failed span; if several are equally deep, the one that ended first
        deepest = max(error_nodes, key=lambda n: (n.depth, -n.span.end_time_ns))
        root_cause = RootCause(
            service_name=deepest.span.service_name,
            span_name=deepest.span.name,
            kind=deepest.span.kind,
            http_status_code=deepest.span.http_status_code,
            status_message=deepest.span.status_message,
        )

    total_ms = trace.duration_ms
    is_error = bool(error_nodes)
    is_slow = total_ms > latency_threshold_ms
    if is_error:
        status = STATUS_ERROR
    elif is_slow:
        status = STATUS_SLOW
    else:
        status = STATUS_SUCCESS

    root = trace.root_span
    return TraceAnalysis(
        trace_id=trace.trace_id,
        root_span_name=root.name if root else "",
        total_latency_ms=total_ms,
        latency_threshold_ms=latency_threshold_ms,
        is_slow=is_slow,
        is_error=is_error,
        status=status,
        services=services,
        bottleneck_service=bottleneck.service_name if bottleneck else None,
        bottleneck_self_time_ms=bottleneck.self_time_ms if bottleneck else 0.0,
        network_ms=network_ms,
        error_span_count=len(error_nodes),
        root_cause=root_cause,
    )


def format_analysis(a: TraceAnalysis) -> str:
    """Readable multi-line summary (used in logs)."""
    lines = [
        f"    status={a.status} total={a.total_latency_ms:.1f}ms "
        f"threshold={a.latency_threshold_ms:.0f}ms"
    ]
    if a.bottleneck_service:
        lines.append(
            f"    bottleneck={a.bottleneck_service} "
            f"({a.bottleneck_self_time_ms:.1f}ms own work)"
        )
    lines.append(f"    network/wait between services={a.network_ms:.1f}ms")
    for s in a.services:
        lines.append(
            f"      {s.service_name:<16} latency={s.duration_ms:8.1f}ms "
            f"self={s.self_time_ms:8.1f}ms errors={s.error_count}"
        )
    if a.root_cause:
        rc = a.root_cause
        http = f" http={rc.http_status_code}" if rc.http_status_code is not None else ""
        lines.append(
            f"    root_cause={rc.service_name} \"{rc.span_name}\"{http} "
            f"({a.error_span_count} error spans in trace)"
        )
    return "\n".join(lines)