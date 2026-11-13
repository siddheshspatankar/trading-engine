"""
Order entry and order book.

Flow for every order: latest price → risk engine (authorization gate) → paper broker.
Orders only ever reach the in-memory paper broker, never a real brokerage account.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from src.api.state import RiskLogEntry, state
from src.api.validation import normalize_symbol
from src.core.models import Order, OrderSide, OrderStatus, OrderType, RiskDecision, TimeInForce

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=1_000_000)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def limit_needs_price(self) -> "OrderRequest":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        return self


def serialize_order(order: Order) -> dict:
    meta = state.order_meta.get(order.id, {})
    return {
        "id": order.id,
        "created_at": order.created_at.isoformat(),
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": float(order.quantity),
        "requested_quantity": meta.get("requested_qty", float(order.quantity)),
        "filled_quantity": float(order.filled_quantity),
        "limit_price": float(order.limit_price) if order.limit_price else None,
        "average_fill_price": float(order.average_fill_price) if order.average_fill_price else None,
        "status": order.status.value,
        "risk_decision": meta.get("decision"),
        "risk_reason": meta.get("reason"),
        "risk_warnings": meta.get("warnings", []),
    }


async def process_open_orders() -> None:
    """Fill resting paper limit orders whose price has been crossed."""
    open_orders = [
        o for o in state.broker.list_orders()
        if o.status == OrderStatus.ACCEPTED and o.order_type == OrderType.LIMIT
    ]
    if not open_orders:
        return
    prices = await state.market.get_latest_prices(sorted({o.symbol for o in open_orders}))
    for order in open_orders:
        price = prices.get(order.symbol)
        if price is None or order.limit_price is None:
            continue
        crossed = (
            Decimal(str(price)) <= order.limit_price
            if order.side == OrderSide.BUY
            else Decimal(str(price)) >= order.limit_price
        )
        if crossed:
            state.broker.set_market_price(order.symbol, Decimal(str(price)))
            await state.broker.submit_order(order)


@router.post("", status_code=status.HTTP_201_CREATED)
async def place_order(req: OrderRequest) -> dict:
    symbol = normalize_symbol(req.symbol)

    # Mark the book to market so risk sees current exposure
    held = list((await state.broker.get_account()).positions.keys())
    prices = await state.market.get_latest_prices(sorted(set(held) | {symbol}))
    dec_prices = {s: Decimal(str(p)) for s, p in prices.items()}
    state.broker.mark_to_market(dec_prices)
    portfolio = await state.broker.get_account()

    order = Order(
        symbol=symbol,
        side=OrderSide(req.side),
        order_type=OrderType(req.order_type),
        quantity=Decimal(req.quantity),
        limit_price=Decimal(str(req.limit_price)) if req.limit_price else None,
        time_in_force=TimeInForce.DAY,
        strategy_id="manual",
    )

    result = await state.risk_engine.evaluate_order(order, portfolio, dec_prices)
    state.order_meta[order.id] = {
        "requested_qty": float(order.quantity),
        "decision": result.decision.value,
        "reason": result.failure_reason,
        "warnings": result.warnings,
    }
    state.record_risk(
        RiskLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            order_id=order.id,
            symbol=symbol,
            side=req.side,
            requested_qty=float(order.quantity),
            authorized_qty=float(result.authorized_quantity),
            decision=result.decision.value,
            reason=result.failure_reason,
            warnings=result.warnings,
            checks=[
                {"name": c.check_name, "passed": c.passed, "message": c.message}
                for c in result.checks
            ],
        )
    )

    if not result.passed:
        rejected = order.model_copy(
            update={"status": OrderStatus.REJECTED, "risk_decision": RiskDecision.REJECTED}
        )
        state.rejected_orders.insert(0, rejected)
        log.info("dashboard_order_rejected_by_risk", symbol=symbol, reason=result.failure_reason)
        return serialize_order(rejected)

    authorized = order.model_copy(
        update={"quantity": result.authorized_quantity, "risk_decision": result.decision}
    )
    state.broker.set_market_price(symbol, dec_prices[symbol])
    submitted = await state.broker.submit_order(authorized)
    if submitted.status == OrderStatus.REJECTED and not state.order_meta[order.id]["reason"]:
        state.order_meta[order.id]["reason"] = "Paper broker rejected the order (insufficient cash)"
    return serialize_order(submitted)


@router.get("")
async def list_orders() -> dict:
    await process_open_orders()
    orders = state.broker.list_orders() + state.rejected_orders
    orders.sort(key=lambda o: o.created_at, reverse=True)
    return {"orders": [serialize_order(o) for o in orders]}


@router.delete("/{order_id}")
async def cancel_order(order_id: str) -> dict:
    if not await state.broker.cancel_order(order_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Order is not open or does not exist")
    return {"id": order_id, "status": "cancelled"}
