"""
User Service - entry point of the TraceFlow demo application.

Request flow: client -> User Service -> Order Service -> Payment Service
"""
import logging
import os
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from telemetry import setup_telemetry

# ---------------------------------------------------------------- configuration
SERVICE_NAME = os.getenv("SERVICE_NAME", "user-service")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8002").rstrip("/")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "15"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="TraceFlow - User Service", version="1.0.0")
setup_telemetry(app, SERVICE_NAME)


# One shared HTTP client for all requests (connection pooling).
# Created on first use, after OpenTelemetry instrumentation is active.
_http_client = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT_SECONDS)
    return _http_client

# ---------------------------------------------------------------- in-memory data
USERS = {
    1: {"id": 1, "name": "Asha Patil", "email": "asha.patil@example.com"},
    2: {"id": 2, "name": "Rohan Mehta", "email": "rohan.mehta@example.com"},
    3: {"id": 3, "name": "Neha Kulkarni", "email": "neha.kulkarni@example.com"},
}

PaymentMode = Literal["normal", "slow", "fail"]


class CheckoutRequest(BaseModel):
    item: str = Field(default="laptop", min_length=1, max_length=100)
    amount: float = Field(default=499.99, gt=0)
    payment_mode: PaymentMode = "normal"


def _upstream_detail(response: httpx.Response):
    """Extract a readable error message from a downstream error response."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    return data.get("detail", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/users")
def list_users():
    return list(USERS.values())


@app.get("/users/{user_id}")
def get_user(user_id: int):
    user = USERS.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@app.post("/users/{user_id}/checkout")
async def checkout(user_id: int, request: CheckoutRequest):
    user = get_user(user_id)  # raises 404 for unknown users
    logger.info(
        "Checkout started user_id=%s item=%s amount=%.2f payment_mode=%s",
        user_id, request.item, request.amount, request.payment_mode,
    )

    payload = {
        "user_id": user_id,
        "item": request.item,
        "amount": request.amount,
        "payment_mode": request.payment_mode,
    }

    try:
        response = await get_http_client().post(f"{ORDER_SERVICE_URL}/orders", json=payload)
    except httpx.TimeoutException:
        logger.error("Order service timed out after %.1fs", DOWNSTREAM_TIMEOUT_SECONDS)
        raise HTTPException(status_code=504, detail="Order service timed out")
    except httpx.RequestError as exc:
        logger.error("Order service unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Order service unavailable")

    if response.status_code >= 400:
        detail = _upstream_detail(response)
        logger.error(
            "Checkout failed user_id=%s upstream_status=%s detail=%s",
            user_id, response.status_code, detail,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Checkout failed in a downstream service",
                "upstream_service": "order-service",
                "upstream_status": response.status_code,
                "upstream_detail": detail,
            },
        )

    order = response.json()
    logger.info("Checkout completed user_id=%s order_id=%s", user_id, order.get("order_id"))
    return {"message": "Checkout successful", "user": user, "order": order}