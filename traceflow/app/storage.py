"""
Trace Storage - SQLite database for TraceFlow.

What is stored:
  traces           one row per KEPT trace (summary + sampling decision + reason)
  spans            the spans of every KEPT trace (for the trace detail view)
  service_metrics  per-service requests / latency / errors over ALL traces
  counters         totals over ALL traces (kept + dropped)

Dropped traces are NOT stored - that is the point of sampling. They only
increase the counters and the service metrics, so the dashboard numbers
still describe 100% of the traffic.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from app.analysis import TraceAnalysis
from app.correlation import CorrelatedTrace
from app.sampling import SamplingDecision

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id                TEXT PRIMARY KEY,
    start_time_ns           INTEGER NOT NULL,
    timestamp               TEXT NOT NULL,
    root_service            TEXT,
    root_operation          TEXT,
    service_path            TEXT,
    span_count              INTEGER NOT NULL,
    total_latency_ms        REAL NOT NULL,
    status                  TEXT NOT NULL,
    is_complete             INTEGER NOT NULL,
    decision                TEXT NOT NULL,
    reason                  TEXT NOT NULL,
    matched_rules           TEXT,
    bottleneck_service      TEXT,
    bottleneck_self_time_ms REAL,
    network_ms              REAL,
    root_cause_service      TEXT,
    root_cause_operation    TEXT,
    root_cause_http_status  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_traces_start ON traces(start_time_ns);

CREATE TABLE IF NOT EXISTS spans (
    trace_id         TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    span_id          TEXT NOT NULL,
    parent_span_id   TEXT,
    position         INTEGER NOT NULL,
    depth            INTEGER NOT NULL,
    service_name     TEXT NOT NULL,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    start_time_ns    INTEGER NOT NULL,
    duration_ms      REAL NOT NULL,
    status_code      TEXT NOT NULL,
    status_message   TEXT,
    http_status_code INTEGER,
    PRIMARY KEY (trace_id, span_id)
);

CREATE TABLE IF NOT EXISTS service_metrics (
    service_name     TEXT PRIMARY KEY,
    request_count    INTEGER NOT NULL DEFAULT 0,
    total_latency_ms REAL NOT NULL DEFAULT 0,
    max_latency_ms   REAL NOT NULL DEFAULT 0,
    error_count      INTEGER NOT NULL DEFAULT 0,
    root_cause_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0
);
"""


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


class TraceStore:
    def __init__(self, db_path: str, max_stored_traces: int = 5000):
        if max_stored_traces < 1:
            raise ValueError("max_stored_traces must be at least 1")
        self.db_path = db_path
        self.max_stored_traces = max_stored_traces
        self._write_lock = threading.Lock()

        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation: simple and thread-safe."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ writes
    def record(self, trace: CorrelatedTrace, analysis: TraceAnalysis, decision: SamplingDecision) -> None:
        """Record one processed trace. Counters and metrics always; full trace only if KEPT."""
        span_count = len(trace.spans)
        with self._write_lock, self._connect() as conn:
            self._add_counters(conn, {
                "traces_total": 1,
                "traces_kept": 1 if decision.keep else 0,
                "traces_dropped": 0 if decision.keep else 1,
                "spans_total": span_count,
                "spans_kept": span_count if decision.keep else 0,
                "error_traces": 1 if analysis.is_error else 0,
                "slow_traces": 1 if analysis.is_slow else 0,
                "incomplete_traces": 0 if trace.is_complete else 1,
                "latency_sum_ms": analysis.total_latency_ms,
            })
            self._update_service_metrics(conn, trace, analysis)
            if decision.keep:
                self._insert_trace(conn, trace, analysis, decision)
                self._enforce_retention(conn)

    @staticmethod
    def _add_counters(conn: sqlite3.Connection, increments: Dict[str, float]) -> None:
        conn.executemany(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
            list(increments.items()),
        )

    @staticmethod
    def _update_service_metrics(conn: sqlite3.Connection, trace: CorrelatedTrace,
                                analysis: TraceAnalysis) -> None:
        for span in trace.spans:
            if span.kind != "SERVER":  # one SERVER span = one request handled by that service
                continue
            conn.execute(
                "INSERT INTO service_metrics "
                "(service_name, request_count, total_latency_ms, max_latency_ms, error_count) "
                "VALUES (?, 1, ?, ?, ?) "
                "ON CONFLICT(service_name) DO UPDATE SET "
                "  request_count = request_count + 1, "
                "  total_latency_ms = total_latency_ms + excluded.total_latency_ms, "
                "  max_latency_ms = MAX(max_latency_ms, excluded.max_latency_ms), "
                "  error_count = error_count + excluded.error_count",
                (span.service_name, span.duration_ms, span.duration_ms, 1 if span.is_error else 0),
            )
        if analysis.root_cause:
            conn.execute(
                "INSERT INTO service_metrics (service_name, root_cause_count) VALUES (?, 1) "
                "ON CONFLICT(service_name) DO UPDATE SET root_cause_count = root_cause_count + 1",
                (analysis.root_cause.service_name,),
            )

    @staticmethod
    def _insert_trace(conn: sqlite3.Connection, trace: CorrelatedTrace,
                      analysis: TraceAnalysis, decision: SamplingDecision) -> None:
        root = trace.root_span
        rc = analysis.root_cause
        # REPLACE: if the same trace_id is stored again, its old spans are removed (cascade)
        conn.execute(
            "INSERT OR REPLACE INTO traces (trace_id, start_time_ns, timestamp, root_service, "
            "root_operation, service_path, span_count, total_latency_ms, status, is_complete, "
            "decision, reason, matched_rules, bottleneck_service, bottleneck_self_time_ms, "
            "network_ms, root_cause_service, root_cause_operation, root_cause_http_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trace.trace_id,
                trace.start_time_ns,
                _iso(trace.start_time_ns),
                root.service_name if root else None,
                root.name if root else None,
                " -> ".join(trace.service_path),
                len(trace.spans),
                round(analysis.total_latency_ms, 2),
                analysis.status,
                1 if trace.is_complete else 0,
                decision.decision,
                decision.reason,
                ",".join(decision.matched_rules),
                analysis.bottleneck_service,
                round(analysis.bottleneck_self_time_ms, 2),
                round(analysis.network_ms, 2),
                rc.service_name if rc else None,
                rc.span_name if rc else None,
                rc.http_status_code if rc else None,
            ),
        )
        conn.executemany(
            "INSERT INTO spans (trace_id, span_id, parent_span_id, position, depth, service_name, "
            "name, kind, start_time_ns, duration_ms, status_code, status_message, http_status_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    trace.trace_id,
                    node.span.span_id,
                    node.span.parent_span_id,
                    position,
                    node.depth,
                    node.span.service_name,
                    node.span.name,
                    node.span.kind,
                    node.span.start_time_ns,
                    round(node.span.duration_ms, 2),
                    node.span.status_code,
                    node.span.status_message,
                    node.span.http_status_code,
                )
                for position, node in enumerate(trace.iter_nodes())  # call order
            ],
        )

    def _enforce_retention(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "DELETE FROM traces WHERE trace_id IN ("
            "  SELECT trace_id FROM traces ORDER BY start_time_ns DESC LIMIT -1 OFFSET ?)",
            (self.max_stored_traces,),
        )

    def reset(self) -> None:
        with self._write_lock, self._connect() as conn:
            for table in ("spans", "traces", "service_metrics", "counters"):
                conn.execute(f"DELETE FROM {table}")

    # ------------------------------------------------------------------- reads
    def summary(self) -> Dict[str, Any]:
        with self._connect() as conn:
            c = {row["name"]: row["value"] for row in conn.execute("SELECT name, value FROM counters")}
            by_reason = {
                row["reason"]: row["n"]
                for row in conn.execute("SELECT reason, COUNT(*) AS n FROM traces GROUP BY reason")
            }
            stored = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]

        total = int(c.get("traces_total", 0))
        spans_total = int(c.get("spans_total", 0))
        dropped = int(c.get("traces_dropped", 0))
        spans_kept = int(c.get("spans_kept", 0))
        return {
            "total_traces": total,
            "retained_traces": int(c.get("traces_kept", 0)),
            "dropped_traces": dropped,
            "error_traces": int(c.get("error_traces", 0)),
            "slow_traces": int(c.get("slow_traces", 0)),
            "incomplete_traces": int(c.get("incomplete_traces", 0)),
            "average_latency_ms": round(c.get("latency_sum_ms", 0) / total, 1) if total else 0.0,
            "spans_total": spans_total,
            "spans_kept": spans_kept,
            "trace_reduction_percent": _pct(dropped, total),
            "span_reduction_percent": _pct(spans_total - spans_kept, spans_total),
            "stored_traces": stored,
            "stored_by_reason": by_reason,
        }

    @staticmethod
    def _trace_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["is_complete"] = bool(data["is_complete"])
        data["matched_rules"] = [r for r in (data["matched_rules"] or "").split(",") if r]
        data["services"] = data["service_path"].split(" -> ") if data["service_path"] else []
        return data

    def list_traces(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM traces"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.upper())
        query += " ORDER BY start_time_ns DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [self._trace_row(row) for row in conn.execute(query, params)]

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
            if row is None:
                return None
            spans = [
                dict(s)
                for s in conn.execute(
                    "SELECT * FROM spans WHERE trace_id = ? ORDER BY position", (trace_id,)
                )
            ]
        data = self._trace_row(row)
        data["spans"] = spans
        return data

    def service_metrics(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM service_metrics ORDER BY service_name").fetchall()
        result = []
        for row in rows:
            count = row["request_count"]
            result.append({
                "service_name": row["service_name"],
                "requests": count,
                "avg_latency_ms": round(row["total_latency_ms"] / count, 1) if count else 0.0,
                "max_latency_ms": round(row["max_latency_ms"], 1),
                "errors": row["error_count"],
                "error_rate_percent": _pct(row["error_count"], count),
                "root_cause_count": row["root_cause_count"],
            })
        return result