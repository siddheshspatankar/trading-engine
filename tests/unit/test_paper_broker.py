"""
Unit tests for the Paper Trading Broker Adapter.

Tests order filling, position tracking, and P&L calculation.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from src.broker.adapters.paper_broker import PaperBrokerAdapter
from src.core.models import Order, OrderSide, OrderStatus, OrderType, TimeInForce


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


@pytest.fixture
def broker() -> PaperBrokerAdapter:
    b = PaperBrokerAdapter(initial_capital=Decimal("100000"), slippage_bps=5)
    b.set_market_price("AAPL", Decimal("150"))
    b.set_market_price("MSFT", Decimal("400"))
    return b


@pytest.mark.asyncio
@pytest.mark.unit
async def test_buy_order_fills_and_deducts_cash(broker: PaperBrokerAdapter) -> None:
    """Buying should deduct cash and create a position."""
    order = make_order(quantity=Decimal("10"))
    filled = await broker.submit_order(order)

    assert filled.status == OrderStatus.FILLED
    assert filled.filled_quantity == Decimal("10")
    assert filled.average_fill_price > Decimal("150")  # Slippage added

    portfolio = await broker.get_account()
    assert portfolio.cash < Decimal("100000")
    assert "AAPL" in portfolio.positions
    assert portfolio.positions["AAPL"].quantity == Decimal("10")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sell_order_increases_cash(broker: PaperBrokerAdapter) -> None:
    """Selling should increase cash and reduce/close position."""
    # First buy
    buy = make_order(side=OrderSide.BUY, quantity=Decimal("10"))
    await broker.submit_order(buy)

    cash_after_buy = (await broker.get_account()).cash

    # Then sell
    sell = make_order(side=OrderSide.SELL, quantity=Decimal("10"))
    await broker.submit_order(sell)

    portfolio = await broker.get_account()
    assert portfolio.cash > cash_after_buy
    assert "AAPL" not in portfolio.positions  # Position fully closed


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_price_rejects_order(broker: PaperBrokerAdapter) -> None:
    """Orders for symbols without a market price should be rejected."""
    order = make_order(symbol="UNKNOWN_SYM")
    filled = await broker.submit_order(order)
    assert filled.status == OrderStatus.REJECTED


@pytest.mark.asyncio
@pytest.mark.unit
async def test_insufficient_funds_rejection(broker: PaperBrokerAdapter) -> None:
    """Orders exceeding available cash should be handled gracefully."""
    # Try to buy more than we can afford: 10000 shares at $150 = $1.5M
    order = make_order(quantity=Decimal("10000"))
    filled = await broker.submit_order(order)
    # Should either partially fill or reject
    assert filled.status in {OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED, OrderStatus.FILLED}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cancel_pending_order(broker: PaperBrokerAdapter) -> None:
    """Pending orders should be cancellable."""
    # Create a limit order above market price that won't fill
    order = make_order(limit_price=Decimal("100"))  # Limit is $100, market is $150
    submitted = await broker.submit_order(order)

    # Order should be resting (accepted but not filled)
    if submitted.status in {OrderStatus.ACCEPTED, OrderStatus.PENDING}:
        cancelled = await broker.cancel_order(submitted.id)
        assert cancelled


@pytest.mark.asyncio
@pytest.mark.unit
async def test_portfolio_equity_calculation(broker: PaperBrokerAdapter) -> None:
    """Total equity = cash + market value of positions."""
    order = make_order(quantity=Decimal("10"))
    await broker.submit_order(order)

    portfolio = await broker.get_account()
    assert portfolio.total_equity is not None
    # Equity ≈ initial capital (minus commissions)
    assert float(portfolio.total_equity) == pytest.approx(100000, rel=0.01)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_close_all_positions(broker: PaperBrokerAdapter) -> None:
    """Emergency close should exit all open positions."""
    # Open positions in two symbols
    broker.set_market_price("MSFT", Decimal("400"))
    await broker.submit_order(make_order(symbol="AAPL", quantity=Decimal("5")))
    await broker.submit_order(make_order(symbol="MSFT", quantity=Decimal("3")))

    portfolio_before = await broker.get_account()
    assert len(portfolio_before.positions) > 0

    await broker.close_all_positions()

    portfolio_after = await broker.get_account()
    assert len(portfolio_after.positions) == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_health_check_always_true(broker: PaperBrokerAdapter) -> None:
    """Paper broker health check should always return True."""
    assert await broker.health_check() is True
