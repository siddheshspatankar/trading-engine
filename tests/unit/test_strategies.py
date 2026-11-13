"""
Unit tests for quantitative strategies.

Uses synthetic OHLCV data to test signal generation without external dependencies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from src.core.models import Portfolio, SignalDirection
from src.strategies.quant.momentum import MomentumStrategy
from src.strategies.quant.mean_reversion import MeanReversionStrategy


def make_portfolio(cash: float = 100000.0) -> Portfolio:
    return Portfolio(cash=Decimal(str(cash)), total_equity=Decimal(str(cash)))


def make_trending_data(
    symbol: str,
    n_days: int = 300,
    trend: float = 0.001,  # 0.1% daily trend
    start_price: float = 100.0,
) -> pd.DataFrame:
    """Generate synthetic trending price data."""
    dates = pd.date_range(end=datetime.utcnow(), periods=n_days, freq="D", tz="UTC")
    prices = [start_price]
    for i in range(1, n_days):
        noise = np.random.normal(0, 0.01)
        prices.append(prices[-1] * (1 + trend + noise))

    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000] * n_days,
        },
        index=dates,
    )
    return df


def make_mean_reverting_data(
    symbol: str, n_days: int = 100, center: float = 100.0
) -> pd.DataFrame:
    """Generate synthetic mean-reverting price data."""
    dates = pd.date_range(end=datetime.utcnow(), periods=n_days, freq="D", tz="UTC")
    prices = [center]
    for i in range(1, n_days):
        mean_pull = -0.1 * (prices[-1] - center)
        noise = np.random.normal(0, 1)
        prices.append(prices[-1] + mean_pull + noise)

    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p + abs(np.random.normal(0, 0.5)) for p in prices],
            "low": [p - abs(np.random.normal(0, 0.5)) for p in prices],
            "close": prices,
            "volume": [1_000_000] * n_days,
        },
        index=dates,
    )
    return df


@pytest.mark.asyncio
@pytest.mark.unit
async def test_momentum_generates_long_signal_for_trending_stock() -> None:
    """Momentum strategy should identify strong uptrending stocks."""
    np.random.seed(42)
    symbols = ["AAPL", "MSFT", "GOOGL"]

    # AAPL is the best performer
    market_data = {
        "AAPL": make_trending_data("AAPL", trend=0.003),   # Strong trend
        "MSFT": make_trending_data("MSFT", trend=0.001),   # Moderate
        "GOOGL": make_trending_data("GOOGL", trend=-0.001), # Weak/declining
    }

    strategy = MomentumStrategy(
        strategy_id="test_momentum",
        config={
            "universe": symbols,
            "lookback_days": 252,
            "skip_days": 21,
            "top_n": 1,
            "bottom_n": 0,
            "min_data_points": 50,
        },
    )

    portfolio = make_portfolio()
    signals = await strategy.execute(market_data, portfolio)

    # Should have at least one signal
    assert len(signals) > 0
    # All signals should be LONG (bottom_n=0, no shorts)
    for sig in signals:
        assert sig.direction == SignalDirection.LONG


@pytest.mark.asyncio
@pytest.mark.unit
async def test_momentum_emits_flat_for_held_symbols_dropped_from_ranking() -> None:
    """When a held symbol drops out of top_n, strategy should emit FLAT signal."""
    np.random.seed(0)
    symbols = ["AAPL", "MSFT"]

    market_data = {
        "AAPL": make_trending_data("AAPL", trend=0.003),
        "MSFT": make_trending_data("MSFT", trend=-0.002),  # underperforming
    }

    strategy = MomentumStrategy(
        strategy_id="test",
        config={"universe": symbols, "top_n": 1, "lookback_days": 252},
    )

    # Portfolio holds MSFT (the loser)
    from src.core.models import Position
    portfolio = Portfolio(
        cash=Decimal("90000"),
        total_equity=Decimal("100000"),
        positions={
            "MSFT": Position(
                symbol="MSFT",
                quantity=Decimal("10"),
                average_cost=Decimal("100"),
                current_price=Decimal("95"),
            )
        },
    )

    signals = await strategy.execute(market_data, portfolio)

    # Should have a FLAT signal for MSFT
    msft_signals = [s for s in signals if s.symbol == "MSFT"]
    assert any(s.direction == SignalDirection.FLAT for s in msft_signals)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_momentum_insufficient_data_returns_no_signals() -> None:
    """When data is insufficient, strategy returns empty signal list."""
    market_data = {
        "AAPL": make_trending_data("AAPL", n_days=10),  # Too few bars
    }

    strategy = MomentumStrategy(
        strategy_id="test",
        config={"universe": ["AAPL"], "lookback_days": 252, "min_data_points": 100},
    )

    signals = await strategy.execute(market_data, make_portfolio())
    assert signals == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mean_reversion_strategy_initializes() -> None:
    """Mean reversion strategy should initialize without error."""
    strategy = MeanReversionStrategy(
        strategy_id="test_mr",
        config={"universe": ["AAPL"], "bb_period": 20, "rsi_period": 14},
    )
    await strategy.initialize()
    await strategy.teardown()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mean_reversion_signal_strength_in_bounds() -> None:
    """All generated signal strengths must be in [0.0, 1.0]."""
    np.random.seed(1)
    market_data = {
        "AAPL": make_mean_reverting_data("AAPL", n_days=100),
    }

    strategy = MeanReversionStrategy(
        strategy_id="test",
        config={"universe": ["AAPL"]},
    )

    signals = await strategy.execute(market_data, make_portfolio())
    for sig in signals:
        assert 0.0 <= sig.strength <= 1.0
