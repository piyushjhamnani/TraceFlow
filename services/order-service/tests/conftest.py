"""Test setup: make app.py importable and switch off span export during tests."""
import os
import sys
from pathlib import Path

os.environ["TRACE_EXPORTER"] = "none"  # tests must not try to reach a Collector
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))