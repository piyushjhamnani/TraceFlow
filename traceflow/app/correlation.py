"""
Trace Correlation - turns the flat list of spans of one trace into a call tree.

All spans of a request share the same trace_id (grouped by the Trace Buffer).
Each span's parent_span_id points to the span that called it, so following
these links rebuilds the request's journey: User -> Order -> Payment.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from app.models import Span


@dataclass
class SpanNode:
    span: Span
    depth: int
    children: List["SpanNode"] = field(default_factory=list)


@dataclass
class CorrelatedTrace:
    trace_id: str
    spans: List[Span]               # sorted by start time
    roots: List[SpanNode]           # normally exactly one
    is_complete: bool
    missing_parent_ids: List[str]   # parents referenced but never received

    @property
    def root_span(self) -> Optional[Span]:
        return self.roots[0].span if self.roots else None

    @property
    def start_time_ns(self) -> int:
        return min(s.start_time_ns for s in self.spans)

    @property
    def end_time_ns(self) -> int:
        return max(s.end_time_ns for s in self.spans)

    @property
    def duration_ms(self) -> float:
        """Total trace latency: first span start -> last span end."""
        return (self.end_time_ns - self.start_time_ns) / 1_000_000

    @property
    def services(self) -> List[str]:
        return sorted({s.service_name for s in self.spans})

    def iter_nodes(self) -> Iterator[SpanNode]:
        """Depth-first walk of the tree (call order)."""
        stack = list(reversed(self.roots))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    @property
    def service_path(self) -> List[str]:
        """Services in call order, e.g. ['user-service', 'order-service', 'payment-service']."""
        path: List[str] = []
        for node in self.iter_nodes():
            name = node.span.service_name
            if not path or path[-1] != name:
                path.append(name)
        return path


def correlate(trace_id: str, spans: List[Span]) -> CorrelatedTrace:
    """Build the parent/child tree for one trace and check its completeness."""
    if not spans:
        raise ValueError("cannot correlate a trace without spans")

    ordered = sorted(spans, key=lambda s: s.start_time_ns)
    span_ids = {s.span_id for s in ordered}

    children: Dict[str, List[Span]] = {}
    root_spans: List[Span] = []
    missing: List[str] = []

    for span in ordered:
        if span.parent_span_id is None:
            root_spans.append(span)                    # real root: request entry point
        elif span.parent_span_id in span_ids:
            children.setdefault(span.parent_span_id, []).append(span)
        else:
            root_spans.append(span)                    # orphan: its parent never arrived
            missing.append(span.parent_span_id)

    def build(span: Span, depth: int) -> SpanNode:
        node = SpanNode(span=span, depth=depth)
        node.children = [build(child, depth + 1) for child in children.get(span.span_id, [])]
        return node

    roots = [build(span, 0) for span in root_spans]

    trace = CorrelatedTrace(
        trace_id=trace_id,
        spans=ordered,
        roots=roots,
        is_complete=False,
        missing_parent_ids=sorted(set(missing)),
    )
    reached = sum(1 for _ in trace.iter_nodes())
    trace.is_complete = (
        len(root_spans) == 1
        and root_spans[0].parent_span_id is None
        and not missing
        and reached == len(ordered)
    )
    return trace


def render_tree(trace: CorrelatedTrace) -> str:
    """Readable multi-line view of the call tree (used in logs)."""
    lines = []
    for node in trace.iter_nodes():
        s = node.span
        indent = "    " * node.depth
        http = f" http={s.http_status_code}" if s.http_status_code is not None else ""
        lines.append(
            f"    {indent}{s.service_name} {s.kind} \"{s.name}\" "
            f"{s.duration_ms:.1f}ms {s.status_code}{http}"
        )
    return "\n".join(lines)