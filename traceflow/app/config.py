"""TraceFlow configuration, read from environment variables."""
import os
from dataclasses import dataclass


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    log_level: str
    trace_idle_timeout_seconds: float     # trace is complete after this long without new spans
    buffer_check_interval_seconds: float  # how often the buffer is checked
    max_buffered_traces: int              # memory safety limit
    latency_threshold_ms: float           # traces slower than this are classified SLOW
    baseline_sample_rate: float           # share of healthy traces kept anyway (0.0 - 1.0)
    keep_incomplete_traces: bool          # keep traces with missing spans
    db_path: str                          # SQLite database file
    max_stored_traces: int                # retention: oldest kept traces are deleted beyond this
    demo_checkout_url: str                # where the dashboard's demo buttons send requests

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            trace_idle_timeout_seconds=float(os.getenv("TRACE_IDLE_TIMEOUT_SECONDS", "10")),
            buffer_check_interval_seconds=float(os.getenv("BUFFER_CHECK_INTERVAL_SECONDS", "1")),
            max_buffered_traces=int(os.getenv("MAX_BUFFERED_TRACES", "10000")),
            latency_threshold_ms=float(os.getenv("LATENCY_THRESHOLD_MS", "2000")),
            baseline_sample_rate=float(os.getenv("BASELINE_SAMPLE_RATE", "0.0")),
            keep_incomplete_traces=_bool("KEEP_INCOMPLETE_TRACES", "true"),
            db_path=os.getenv("TRACEFLOW_DB_PATH", os.path.join("data", "traceflow.db")),
            max_stored_traces=int(os.getenv("MAX_STORED_TRACES", "5000")),
            demo_checkout_url=os.getenv(
                "DEMO_CHECKOUT_URL", "http://127.0.0.1:8001/users/1/checkout"
            ),
        )


settings = Settings.from_env()