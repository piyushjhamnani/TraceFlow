"""
Payment Service - simulates a payment gateway.

Supports three modes so TraceFlow can be demonstrated reliably:
  normal -> fast success (~50-150 ms)
  slow   -> success after PAYMENT_SLOW_DELAY_SECONDS (default 5 s)
  fail   -> HTTP 500 (simulated gateway failure)
"""
import asyncio
import logging
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from telemetry import setup_telemetry

# ---------------------------------------------------------------- configuration
SERVICE_NAME = os.getenv("SERVICE_NAME", "payment-service")
PAYMENT_SLOW_DELAY_SECONDS = float(os.getenv("PAYMENT_SLOW_DELAY_SECONDS", "5"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="TraceFlow - Payment Service", version="1.0.0")
setup_telemetry(app, SERVICE_NAME)

PaymentMode = Literal["normal", "slow", "fail"]


class PaymentRequest(BaseModel):
    order_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    mode: PaymentMode = "normal"


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/payments", status_code=201)
async def process_payment(request: PaymentRequest):
    logger.info("Payment requested order_id=%s amount=%.2f mode=%s",
                request.order_id, request.amount, request.mode)

    if request.mode == "slow":
        logger.warning("Simulating slow payment gateway delay=%.1fs order_id=%s",
                       PAYMENT_SLOW_DELAY_SECONDS, request.order_id)
        await asyncio.sleep(PAYMENT_SLOW_DELAY_SECONDS)
    else:
        # realistic small processing time
        await asyncio.sleep(random.uniform(0.05, 0.15))

    if request.mode == "fail":
        logger.error("Payment gateway failure order_id=%s", request.order_id)
        raise HTTPException(
            status_code=500,
            detail="Payment gateway error: transaction declined (simulated failure)",
        )

    payment = {
        "payment_id": f"PAY-{uuid.uuid4().hex[:8].upper()}",
        "order_id": request.order_id,
        "amount": request.amount,
        "status": "APPROVED",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Payment approved order_id=%s payment_id=%s",
                request.order_id, payment["payment_id"])
    return payment