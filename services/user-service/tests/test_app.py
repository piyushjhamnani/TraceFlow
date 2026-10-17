"""Tests for the User Service. The Order Service is replaced by a fake (httpx.MockTransport)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app as service


@pytest.fixture
def client():
    with TestClient(service.app) as test_client:
        yield test_client


def fake_order_service(monkeypatch, handler):
    """Send the service's outgoing HTTP calls to `handler` instead of the network."""
    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(service, "_http_client", fake_client)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "user-service"}


def test_get_existing_user(client):
    response = client.get("/users/1")
    assert response.status_code == 200
    assert response.json()["name"] == "Asha Patil"


def test_unknown_user_returns_404(client):
    assert client.get("/users/999").status_code == 404


def test_checkout_success(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/orders"
        assert json.loads(request.content)["payment_mode"] == "normal"
        return httpx.Response(201, json={"order_id": "ORD-TEST", "status": "CONFIRMED"})

    fake_order_service(monkeypatch, handler)
    response = client.post("/users/1/checkout", json={"payment_mode": "normal"})
    assert response.status_code == 200
    assert response.json()["order"]["order_id"] == "ORD-TEST"


def test_checkout_downstream_failure_returns_502(client, monkeypatch):
    def handler(request):
        return httpx.Response(502, json={"detail": {"message": "Payment failed"}})

    fake_order_service(monkeypatch, handler)
    response = client.post("/users/1/checkout", json={"payment_mode": "fail"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["upstream_service"] == "order-service"
    assert detail["upstream_status"] == 502


def test_checkout_order_service_down_returns_503(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    fake_order_service(monkeypatch, handler)
    response = client.post("/users/1/checkout", json={"payment_mode": "normal"})
    assert response.status_code == 503


def test_checkout_rejects_invalid_mode(client):
    response = client.post("/users/1/checkout", json={"payment_mode": "explode"})
    assert response.status_code == 422


def test_checkout_unknown_user_returns_404(client):
    response = client.post("/users/999/checkout", json={"payment_mode": "normal"})
    assert response.status_code == 404