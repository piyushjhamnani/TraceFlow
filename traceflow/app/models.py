"""Data model used inside TraceFlow."""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Span:
    trace_id: str                  # 32 hex chars, shared by all spans of one request
    span_id: str                   # 16 hex chars, unique per span
    parent_span_id: Optional[str]  # None for the root span
    name: str                      # e.g. "POST /payments"
    kind: str                      # SERVER, CLIENT, INTERNAL, ...
    service_name: str              # e.g. "payment-service"
    start_time_ns: int
    end_time_ns: int
    status_code: str               # UNSET, OK or ERROR
    status_message: str = ""
    http_status_code: Optional[int] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end_time_ns - self.start_time_ns) / 1_000_000

    @property
    def is_error(self) -> bool:
        return self.status_code == "ERROR"