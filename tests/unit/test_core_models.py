"""
Unit tests for core domain models.

Validates model constraints, computed properties, and serialization.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from decimal import Decimal

from src.core.models import (
    AssetClass,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    Price,
    Quote,
    Signal,
    SignalDirection,
    Symbol,
    TimeInForce,
)


@pytest.mark.unit
def test_symbol_ticker_uppercased() -> None:
    """Symbol ticker should always be uppercased and stripped."""
    sym = Symbol(ticker=" aapl ")
    assert sym.ticker == "AAPL"


@pytest.mark.unit
def test_symbol_hash_by_ticker() -> None:
    """Symbols with the same ticker should hash the same."""
    s1 = Symbol(ticker="AAPL")
    s2 = Symbol(ticker="AAPL")
    assert hash(s1) == hash(s2)
    assert {s1, s2} == {s1}  # Set deduplication


@pytest.mark.unit
def test_quote_mid_and_spread() -> None:
    """Quote mid-price and spread should be computed correctly."""
    quote = Quote(
        symbol="AAPL",
        timestamp=datetime.utcnow(),
        bid=Decimal("149.95"),
        ask=Decimal("150.05"),
        bid_size=Decimal("100"),
        ask_size=Decimal("200"),
    )
    assert quote.mid == Decimal("150.00")
    assert quote.spread == Decimal("0.10")
    assert float(quote.spread_bps) == pytest.approx(6.67, abs=0.1)


@pytest.mark.unit
def test_position_market_value_and_unrealized_pnl() -> None:
    """Position computed properties should be accurate."""
    pos = Position(
        symbol="AAPL",
        quantity=Decimal("100"),
        average_cost=Decimal("140"),
        current_price=Decimal("150"),
    )
    assert pos.market_value == Decimal("15000")
    assert pos.unrealized_pnl == Decimal("1000")
    assert pos.unrealized_pnl_pct == pytest.approx(0.0714, abs=0.001)
    assert pos.is_long
    assert not pos.is_short


@pytest.mark.unit
def test_short_position() -> None:
    """Short positions (negative quantity) should report correctly."""
    pos = Position(
        symbol="TSLA",
        quantity=Decimal("-50"),
        average_cost=Decimal("200"),
        current_price=Decimal("180"),
    )
    assert pos.is_short
    assert not pos.is_long
    # Short P&L: (180 - 200) * -50 = (-20) * -50 = +1000
    assert pos.unrealized_pnl == Decimal("1000")


@pytest.mark.unit
def test_order_remaining_quantity() -> None:
    """Order remaining quantity = total - filled."""
    order = Order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("100"),
        filled_quantity=Decimal("40"),
        time_in_force=TimeInForce.DAY,
    )
    assert order.remaining_quantity == Decimal("60")


@pytest.mark.unit
def test_order_terminal_status() -> None:
    """Terminal orders should not be modified further."""
    for terminal_status in [
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    ]:
        order = Order(
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
            status=terminal_status,
            time_in_force=TimeInForce.DAY,
        )
        assert order.is_terminal

    pending_order = Order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        status=OrderStatus.PENDING,
        time_in_force=TimeInForce.DAY,
    )
    assert not pending_order.is_terminal


@pytest.mark.unit
def test_fill_gross_and_net_value() -> None:
    """Fill value calculations should account for commission."""
    fill = Fill(
        order_id="test-order",
        timestamp=datetime.utcnow(),
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("150"),
        commission=Decimal("1.50"),
    )
    assert fill.gross_value == Decimal("1500")
    assert fill.net_value == Decimal("1501.50")


@pytest.mark.unit
def test_portfolio_total_market_value() -> None:
    """Portfolio should sum market values of all positions."""
    portfolio = Portfolio(
        cash=Decimal("50000"),
        total_equity=Decimal("100000"),
        positions={
            "AAPL": Position(
                symbol="AAPL",
                quantity=Decimal("100"),
                average_cost=Decimal("140"),
                current_price=Decimal("150"),
            ),
            "MSFT": Position(
                symbol="MSFT",
                quantity=Decimal("50"),
                average_cost=Decimal("390"),
                current_price=Decimal("400"),
            ),
        },
    )
    # AAPL: 100 * 150 = 15000
    # MSFT: 50 * 400 = 20000
    assert portfolio.total_market_value == Decimal("35000")


@pytest.mark.unit
def test_signal_strength_bounds() -> None:
    """Signal strength must be between 0.0 and 1.0."""
    with pytest.raises(Exception):  # pydantic ValidationError
        Signal(
            source="test",
            symbol="AAPL",
            direction=SignalDirection.LONG,
            strength=1.5,  # Invalid — must be <= 1.0
        )

    with pytest.raises(Exception):
        Signal(
            source="test",
            symbol="AAPL",
            direction=SignalDirection.LONG,
            strength=-0.1,  # Invalid — must be >= 0.0
        )
