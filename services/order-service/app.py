"""
Order Service - creates orders and requests payment from the Payment Service.
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from telemetry import setup_telemetry

# ---------------------------------------------------------------- configuration
SERVICE_NAME = os.getenv("SERVICE_NAME", "order-service")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003").rstrip("/")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "10"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="TraceFlow - Order Service", version="1.0.0")
setup_telemetry(app, SERVICE_NAME)


# One shared HTTP client for all requests (connection pooling).
# Created on first use, after OpenTelemetry instrumentation is active.
_http_client = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT_SECONDS)
    return _http_client

# In-memory order store (resets on restart - fine for a demo service)
ORDERS = {}

PaymentMode = Literal["normal", "slow", "fail"]


class OrderRequest(BaseModel):
    user_id: int = Field(gt=0)
    item: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0)
    payment_mode: PaymentMode = "normal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upstream_detail(response: httpx.Response):
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    return data.get("detail", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/orders")
def list_orders():
    return list(ORDERS.values())


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return order


@app.post("/orders", status_code=201)
async def create_order(request: OrderRequest):
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = {
        "order_id": order_id,
        "user_id": request.user_id,
        "item": request.item,
        "amount": request.amount,
        "payment_mode": request.payment_mode,
        "status": "PENDING_PAYMENT",
        "created_at": _now(),
    }
    ORDERS[order_id] = order
    logger.info("Order created order_id=%s user_id=%s amount=%.2f",
                order_id, request.user_id, request.amount)

    payload = {"order_id": order_id, "amount": request.amount, "mode": request.payment_mode}

    try:
        response = await get_http_client().post(f"{PAYMENT_SERVICE_URL}/payments", json=payload)
    except httpx.TimeoutException:
        order["status"] = "PAYMENT_TIMEOUT"
        logger.error("Payment service timed out order_id=%s", order_id)
        raise HTTPException(status_code=504, detail="Payment service timed out")
    except httpx.RequestError as exc:
        order["status"] = "PAYMENT_SERVICE_UNAVAILABLE"
        logger.error("Payment service unreachable order_id=%s error=%s", order_id, exc)
        raise HTTPException(status_code=503, detail="Payment service unavailable")

    if response.status_code >= 400:
        order["status"] = "PAYMENT_FAILED"
        detail = _upstream_detail(response)
        logger.error("Payment failed order_id=%s upstream_status=%s detail=%s",
                     order_id, response.status_code, detail)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Payment failed",
                "order_id": order_id,
                "upstream_service": "payment-service",
                "upstream_status": response.status_code,
                "upstream_detail": detail,
            },
        )

    order["status"] = "CONFIRMED"
    order["payment"] = response.json()
    logger.info("Order confirmed order_id=%s payment_id=%s",
                order_id, order["payment"].get("payment_id"))
    return order