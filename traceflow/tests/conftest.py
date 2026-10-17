"""Test setup for the TraceFlow engine.

Environment variables are set BEFORE the app modules are imported,
because app.config reads them at import time."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["TRACEFLOW_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="traceflow-test-"), "test.db")
os.environ["LATENCY_THRESHOLD_MS"] = "2000"
os.environ["TRACE_IDLE_TIMEOUT_SECONDS"] = "60"   # background loop never fires during tests
os.environ["BASELINE_SAMPLE_RATE"] = "0.0"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))