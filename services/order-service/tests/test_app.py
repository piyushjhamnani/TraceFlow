"""Tests for the Order Service. The Payment Service is replaced by a fake (httpx.MockTransport)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app as service

ORDER = {"user_id": 1, "item": "laptop", "amount": 499.99, "payment_mode": "normal"}


@pytest.fixture
def client():
    with TestClient(service.app) as test_client:
        yield test_client


def fake_payment_service(monkeypatch, handler):
    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(service, "_http_client", fake_client)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "order-service"


def test_order_confirmed_when_payment_succeeds(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/payments"
        body = json.loads(request.content)
        return httpx.Response(201, json={"payment_id": "PAY-TEST", "order_id": body["order_id"],
                                         "status": "APPROVED"})

    fake_payment_service(monkeypatch, handler)
    response = client.post("/orders", json=ORDER)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "CONFIRMED"
    assert order["payment"]["payment_id"] == "PAY-TEST"
    assert client.get(f"/orders/{order['order_id']}").json()["status"] == "CONFIRMED"


def test_payment_failure_marks_order_failed(client, monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"detail": "Payment gateway error"})

    fake_payment_service(monkeypatch, handler)
    response = client.post("/orders", json={**ORDER, "payment_mode": "fail"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["upstream_status"] == 500
    assert client.get(f"/orders/{detail['order_id']}").json()["status"] == "PAYMENT_FAILED"


def test_payment_service_down_returns_503(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    fake_payment_service(monkeypatch, handler)
    response = client.post("/orders", json=ORDER)
    assert response.status_code == 503
    assert client.get("/orders").json()[-1]["status"] == "PAYMENT_SERVICE_UNAVAILABLE"


def test_unknown_order_returns_404(client):
    assert client.get("/orders/ORD-NOPE").status_code == 404


def test_invalid_amount_rejected(client):
    assert client.post("/orders", json={**ORDER, "amount": 0}).status_code == 422