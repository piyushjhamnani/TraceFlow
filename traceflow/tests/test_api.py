"""TraceFlow HTTP API: OTLP intake, dashboard API and dashboard page."""
import gzip

import pytest
from fastapi.testclient import TestClient

from app import main
from app.buffer import BufferedTrace
from helpers import TRACE_ID, checkout_trace, otlp_request_bytes

PROTOBUF = {"Content-Type": "application/x-protobuf"}
NORMAL, FAILED = "a" * 32, "c" * 32


@pytest.fixture
def client():
    main.store.reset()
    with TestClient(main.app) as test_client:
        yield test_client


def process(trace_id, **kwargs):
    """Run a trace through the full pipeline: correlate -> analyze -> sample -> store."""
    main.handle_completed_trace(
        BufferedTrace(trace_id=trace_id, first_seen=0, last_seen=0,
                      spans=checkout_trace(trace_id, **kwargs))
    )


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_otlp_spans_go_into_the_buffer(client):
    response = client.post("/v1/traces", content=otlp_request_bytes(), headers=PROTOBUF)
    assert response.status_code == 200
    buffered = {t["trace_id"]: t["span_count"] for t in client.get("/api/buffer").json()["traces"]}
    assert buffered[TRACE_ID] == 2


def test_gzip_compressed_otlp_is_accepted(client):
    body = gzip.compress(otlp_request_bytes("9" * 32))
    response = client.post("/v1/traces", content=body,
                           headers={**PROTOBUF, "Content-Encoding": "gzip"})
    assert response.status_code == 200


def test_json_content_type_is_rejected(client):
    response = client.post("/v1/traces", json={"spans": []})
    assert response.status_code == 415


def test_invalid_protobuf_is_rejected(client):
    response = client.post("/v1/traces", content=b"\xff\xff\xff", headers=PROTOBUF)
    assert response.status_code == 400


def test_pipeline_keeps_error_and_drops_normal(client):
    process(NORMAL, payment_ms=100)
    process(FAILED, fail=True)

    summary = client.get("/api/summary").json()
    assert (summary["total_traces"], summary["retained_traces"], summary["dropped_traces"]) == (2, 1, 1)

    traces = client.get("/api/traces").json()
    assert [t["trace_id"] for t in traces] == [FAILED]
    assert traces[0]["decision"] == "KEEP" and traces[0]["reason"] == "ERROR"

    detail = client.get(f"/api/traces/{FAILED}").json()
    assert len(detail["spans"]) == 5
    assert detail["root_cause_service"] == "payment-service"

    assert client.get(f"/api/traces/{NORMAL}").status_code == 404   # dropped


def test_invalid_status_filter_is_rejected(client):
    assert client.get("/api/traces?status=BROKEN").status_code == 422


def test_dashboard_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TraceFlow" in response.text