"""Tests for the Payment Service: the three demo modes."""
import time

import pytest
from fastapi.testclient import TestClient

import app as service

PAYMENT = {"order_id": "ORD-TEST", "amount": 499.99}


@pytest.fixture
def client():
    with TestClient(service.app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "payment-service"


def test_normal_payment_is_approved(client):
    response = client.post("/payments", json={**PAYMENT, "mode": "normal"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["payment_id"].startswith("PAY-")


def test_slow_payment_is_delayed(client, monkeypatch):
    monkeypatch.setattr(service, "PAYMENT_SLOW_DELAY_SECONDS", 0.3)  # shorter than 5 s for tests
    started = time.perf_counter()
    response = client.post("/payments", json={**PAYMENT, "mode": "slow"})
    elapsed = time.perf_counter() - started
    assert response.status_code == 201
    assert elapsed >= 0.3


def test_failed_payment_returns_500(client):
    response = client.post("/payments", json={**PAYMENT, "mode": "fail"})
    assert response.status_code == 500
    assert "Payment gateway" in response.json()["detail"]


def test_invalid_mode_rejected(client):
    assert client.post("/payments", json={**PAYMENT, "mode": "explode"}).status_code == 422


def test_negative_amount_rejected(client):
    assert client.post("/payments", json={**PAYMENT, "amount": -5, "mode": "normal"}).status_code == 422