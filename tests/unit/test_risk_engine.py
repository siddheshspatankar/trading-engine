"""
Unit tests for the Risk Engine.

Tests all risk checks in isolation using the paper broker and synthetic portfolios.
No external dependencies — fully deterministic.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import datetime

from src.core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    RiskDecision,
    TimeInForce,
)
from src.risk.engine.risk_engine import RiskEngine


def make_order(
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("10"),
    limit_price: Decimal | None = None,
) -> Order:
    return Order(
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
        quantity=quantity,
        limit_price=limit_price,
        time_in_force=TimeInForce.DAY,
    )


def make_portfolio(
    cash: Decimal = Decimal("100000"),
    total_equity: Decimal = Decimal("100000"),
    positions: dict | None = None,
    daily_pnl: Decimal | None = None,
) -> Portfolio:
    return Portfolio(
        cash=cash,
        total_equity=total_equity,
        positions=positions or {},
        daily_pnl=daily_pnl,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_trading_halt_rejects_orders() -> None:
    """When trading is halted, all orders must be rejected."""
    engine = RiskEngine()
    engine.halt_trading("manual test halt")

    order = make_order(quantity=Decimal("10"))
    portfolio = make_portfolio()
    prices = {"AAPL": Decimal("150")}

    result = await engine.evaluate_order(order, portfolio, prices)

    assert not result.passed
    assert result.decision == RiskDecision.REJECTED
    assert "halted" in result.failure_reason.lower()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_trading_halt_can_be_resumed() -> None:
    """After resuming from halt, orders should pass basic checks."""
    engine = RiskEngine()
    engine.halt_trading("test halt")
    engine.resume_trading()
    assert not engine.is_halted


@pytest.mark.asyncio
@pytest.mark.unit
async def test_daily_loss_limit_triggers_halt() -> None:
    """When daily loss exceeds the limit, orders are rejected and trading halts."""
    engine = RiskEngine()

    # 5% daily loss on $100k = -$5000, limit is 3%
    portfolio = make_portfolio(
        total_equity=Decimal("97000"),
        daily_pnl=Decimal("-5000"),  # -5% daily loss
    )
    order = make_order()
    prices = {"AAPL": Decimal("150")}

    result = await engine.evaluate_order(order, portfolio, prices)

    assert not result.passed
    assert result.decision == RiskDecision.REJECTED
    assert engine.is_halted  # Should have triggered auto-halt


@pytest.mark.asyncio
@pytest.mark.unit
async def test_position_size_limit_reduces_quantity() -> None:
    """When a position would exceed max_position_pct, quantity is reduced."""
    engine = RiskEngine()

    # Try to buy $20k of AAPL on a $100k portfolio (20% > 10% limit)
    portfolio = make_portfolio(total_equity=Decimal("100000"))
    prices = {"AAPL": Decimal("100")}
    order = make_order(quantity=Decimal("200"), limit_price=Decimal("100"))  # $20k

    result = await engine.evaluate_order(order, portfolio, prices)

    # Should be MODIFIED (approved with reduced size) not rejected
    assert result.passed
    assert result.decision == RiskDecision.MODIFIED
    assert result.authorized_quantity <= Decimal("100")  # max 10% = $10k = 100 shares


@pytest.mark.asyncio
@pytest.mark.unit
async def test_position_size_limit_already_at_max() -> None:
    """When existing position is already at the limit, new orders are rejected."""
    engine = RiskEngine()

    # Already holding 10% in AAPL
    portfolio = make_portfolio(
        total_equity=Decimal("100000"),
        positions={
            "AAPL": Position(
                symbol="AAPL",
                quantity=Decimal("100"),
                average_cost=Decimal("100"),
                current_price=Decimal("100"),
            )
        },
    )
    prices = {"AAPL": Decimal("100")}
    order = make_order(symbol="AAPL", quantity=Decimal("10"), limit_price=Decimal("100"))

    result = await engine.evaluate_order(order, portfolio, prices)

    assert not result.passed
    assert result.decision == RiskDecision.REJECTED


@pytest.mark.asyncio
@pytest.mark.unit
async def test_drawdown_blocks_new_longs() -> None:
    """When drawdown exceeds limit, no new long entries are allowed."""
    engine = RiskEngine()

    # Simulate 20% drawdown (limit is 15%)
    engine._peak_equity = Decimal("100000")
    portfolio = make_portfolio(total_equity=Decimal("80000"))
    prices = {"AAPL": Decimal("100")}
    order = make_order(side=OrderSide.BUY, quantity=Decimal("10"))

    result = await engine.evaluate_order(order, portfolio, prices)

    assert not result.passed
    assert "drawdown" in result.failure_reason.lower()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_drawdown_allows_sells() -> None:
    """During high drawdown, sell orders (exits) should still be allowed."""
    engine = RiskEngine()
    engine._peak_equity = Decimal("100000")

    portfolio = make_portfolio(
        total_equity=Decimal("80000"),
        positions={
            "AAPL": Position(
                symbol="AAPL",
                quantity=Decimal("10"),
                average_cost=Decimal("150"),
                current_price=Decimal("100"),
            )
        },
    )
    prices = {"AAPL": Decimal("100")}
    order = make_order(side=OrderSide.SELL, quantity=Decimal("10"))  # Exit position

    result = await engine.evaluate_order(order, portfolio, prices)

    # Sell should pass despite drawdown
    assert result.passed


@pytest.mark.asyncio
@pytest.mark.unit
async def test_healthy_order_passes_all_checks() -> None:
    """A properly sized order on a healthy portfolio should pass all checks."""
    engine = RiskEngine()
    engine._peak_equity = Decimal("100000")

    portfolio = make_portfolio(
        total_equity=Decimal("100000"),
        daily_pnl=Decimal("500"),  # Positive daily P&L
    )
    prices = {"AAPL": Decimal("150")}
    order = make_order(quantity=Decimal("5"), limit_price=Decimal("150"))  # $750 = 0.75%

    result = await engine.evaluate_order(order, portfolio, prices)

    assert result.passed
    assert result.decision == RiskDecision.APPROVED
    assert result.authorized_quantity == Decimal("5")
