"""
TraceFlow engine - receives spans from the OpenTelemetry Collector (OTLP/HTTP)
and processes each trace once it is complete.

Stage 8 : OTLP receiver + Trace Buffer
Stage 9 : Trace Correlation
Stage 10: Latency & Error Analysis
Stage 11: Tail-Based Sampling
Stage 12: Storage (SQLite) + query API
Stage 13: Dashboard + demo trigger
"""
import asyncio
import contextlib
import gzip
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from app.analysis import analyze, format_analysis
from app.buffer import BufferedTrace, TraceBuffer
from app.config import settings
from app.correlation import correlate, render_tree
from app.receiver import parse_otlp_traces
from app.sampling import SamplingPolicy, decide
from app.storage import TraceStore

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
logger = logging.getLogger("traceflow")

DASHBOARD_FILE = Path(__file__).parent / "dashboard" / "index.html"
VALID_STATUSES = {"SUCCESS", "SLOW", "ERROR"}
DEMO_MODES = {"normal", "slow", "fail"}

buffer = TraceBuffer(settings.trace_idle_timeout_seconds, settings.max_buffered_traces)
policy = SamplingPolicy(
    baseline_sample_rate=settings.baseline_sample_rate,
    keep_incomplete_traces=settings.keep_incomplete_traces,
)
store = TraceStore(settings.db_path, settings.max_stored_traces)


def handle_completed_trace(buffered: BufferedTrace) -> None:
    """Called once per complete trace: correlate -> analyze -> sample -> store."""
    # Stage 9: rebuild the call tree
    trace = correlate(buffered.trace_id, buffered.spans)
    logger.info(
        "Trace correlated trace_id=%s spans=%d path=%s complete=%s%s\n%s",
        trace.trace_id,
        len(trace.spans),
        " -> ".join(trace.service_path),
        "yes" if trace.is_complete else "NO",
        " (forced: buffer full)" if buffered.forced else "",
        render_tree(trace),
    )
    if not trace.is_complete:
        logger.warning(
            "Trace incomplete trace_id=%s roots=%d missing_parents=%s",
            trace.trace_id,
            len(trace.roots),
            ",".join(trace.missing_parent_ids) or "-",
        )

    # Stage 10: latency & error analysis
    analysis = analyze(trace, settings.latency_threshold_ms)
    logger.info("Trace analyzed trace_id=%s\n%s", trace.trace_id, format_analysis(analysis))
    if analysis.root_cause:
        logger.error(
            "Error trace detected trace_id=%s root_cause=%s operation=\"%s\"",
            trace.trace_id,
            analysis.root_cause.service_name,
            analysis.root_cause.span_name,
        )

    # Stage 11: tail-based sampling
    decision = decide(analysis, trace.is_complete, policy)

    # Stage 12: storage (full trace only if KEPT; counters always)
    store.record(trace, analysis, decision)

    logger.info(
        "Sampling decision: %s reason=%s trace_id=%s status=%s total=%.1fms stored=%s",
        decision.decision,
        decision.reason,
        trace.trace_id,
        analysis.status,
        analysis.total_latency_ms,
        "yes" if decision.keep else "no",
    )


async def flush_loop() -> None:
    """Background task: regularly release complete traces from the buffer."""
    while True:
        await asyncio.sleep(settings.buffer_check_interval_seconds)
        for trace in buffer.pop_ready():
            try:
                handle_completed_trace(trace)
            except Exception:
                logger.exception("Error while processing trace trace_id=%s", trace.trace_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "TraceFlow engine started idle_timeout=%.1fs latency_threshold=%.0fms "
        "baseline_sample_rate=%.2f keep_incomplete=%s db=%s",
        settings.trace_idle_timeout_seconds,
        settings.latency_threshold_ms,
        settings.baseline_sample_rate,
        settings.keep_incomplete_traces,
        settings.db_path,
    )
    task = asyncio.create_task(flush_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="TraceFlow Engine", version="1.0.0", lifespan=lifespan)


# ------------------------------------------------------------------ dashboard
@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD_FILE, headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------------- health
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "traceflow-engine",
        "buffered_traces": len(buffer),
        "latency_threshold_ms": settings.latency_threshold_ms,
        "db_path": settings.db_path,
    }


# ------------------------------------------------------------- dashboard API
@app.get("/api/summary")
def api_summary():
    """Numbers for the dashboard cards (over ALL traces, kept + dropped)."""
    return {
        **store.summary(),
        "buffered_traces": len(buffer),
        "latency_threshold_ms": settings.latency_threshold_ms,
        "baseline_sample_rate": settings.baseline_sample_rate,
    }


@app.get("/api/traces")
def api_traces(
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, description="SUCCESS, SLOW or ERROR"),
):
    """Kept traces, newest first. Optional filter: ?status=ERROR"""
    if status is not None:
        status = status.upper()
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail="status must be SUCCESS, SLOW or ERROR")
    return store.list_traces(limit=limit, status=status)


@app.get("/api/traces/{trace_id}")
def api_trace_detail(trace_id: str):
    trace = store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail="Trace not found - it was probably dropped by sampling (normal trace).",
        )
    return trace


@app.get("/api/services")
def api_services():
    """Per-service requests, latency and errors (over ALL traces)."""
    return store.service_metrics()


@app.post("/api/reset")
def api_reset():
    """Clear all stored data (useful before a demo)."""
    store.reset()
    logger.warning("All stored TraceFlow data was cleared")
    return {"status": "cleared"}


@app.post("/api/demo/checkout")
async def api_demo_checkout(mode: str = Query("normal", description="normal, slow or fail")):
    """Dashboard demo buttons: sends a REAL checkout request to the User Service.
    The request then produces a real trace like any other request."""
    if mode not in DEMO_MODES:
        raise HTTPException(status_code=422, detail="mode must be normal, slow or fail")

    payload = {"item": "laptop", "amount": 499.99, "payment_mode": mode}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(settings.demo_checkout_url, json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Demo application not reachable at {settings.demo_checkout_url} ({exc})",
        )
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info("Demo request sent mode=%s http_status=%d duration=%.1fms",
                mode, response.status_code, duration_ms)
    return {"mode": mode, "http_status": response.status_code, "duration_ms": round(duration_ms, 1)}


@app.get("/api/buffer")
def buffer_contents():
    """Traces currently waiting in the buffer (real data, useful during the demo)."""
    return {
        "idle_timeout_seconds": settings.trace_idle_timeout_seconds,
        "traces": buffer.snapshot(),
    }


# --------------------------------------------------------------- OTLP intake
@app.post("/v1/traces")
async def receive_traces(request: Request):
    """OTLP/HTTP endpoint - the Collector's otlphttp exporter posts spans here."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-protobuf":
        raise HTTPException(status_code=415, detail="TraceFlow accepts OTLP/HTTP protobuf only")

    body = await request.body()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError:
            raise HTTPException(status_code=400, detail="Invalid gzip body")

    try:
        spans = parse_otlp_traces(body)
    except DecodeError:
        raise HTTPException(status_code=400, detail="Invalid OTLP protobuf payload")

    added = buffer.add_spans(spans)
    logger.info("Spans received count=%d buffered_traces=%d", added, len(buffer))

    return Response(
        content=ExportTraceServiceResponse().SerializeToString(),
        media_type="application/x-protobuf",
    )