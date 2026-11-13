"""
Mean Reversion Strategy (Bollinger Bands + RSI).

Buys when price is significantly below its moving average with oversold RSI,
sells when price is above its moving average with overbought RSI.

Best for range-bound, low-volatility environments.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.core.models import Portfolio, Signal, SignalDirection
from src.strategies.base.strategy import BaseStrategy, register_strategy

log = structlog.get_logger(__name__)


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI without external dependencies."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(span=period, min_periods=period).mean()
    avg_loss = loss.ewm(span=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bollinger_bands(
    closes: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, middle, lower) Bollinger Bands."""
    middle = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


@register_strategy("mean_reversion")
class MeanReversionStrategy(BaseStrategy):
    """
    Single-asset mean reversion using Bollinger Bands and RSI.

    Configuration keys:
        universe: List of symbols
        bb_period: Bollinger Band period (default: 20)
        bb_std: Standard deviation multiplier (default: 2.0)
        rsi_period: RSI period (default: 14)
        rsi_oversold: RSI threshold to consider oversold (default: 30)
        rsi_overbought: RSI threshold to consider overbought (default: 70)
        min_data_points: Minimum bars required (default: 50)
    """

    @property
    def name(self) -> str:
        return "mean_reversion"

    @property
    def universe(self) -> list[str]:
        return self.config.get("universe", [])

    async def initialize(self) -> None:
        self._log.info("strategy_initialized")

    async def teardown(self) -> None:
        self._log.info("strategy_stopped")

    async def generate_signals(
        self,
        market_data: dict[str, pd.DataFrame],
        portfolio: Portfolio,
    ) -> list[Signal]:
        """Generate mean-reversion signals based on Bollinger Bands + RSI."""
        bb_period = self.config.get("bb_period", 20)
        bb_std = self.config.get("bb_std", 2.0)
        rsi_period = self.config.get("rsi_period", 14)
        rsi_oversold = self.config.get("rsi_oversold", 30)
        rsi_overbought = self.config.get("rsi_overbought", 70)
        min_points = self.config.get("min_data_points", 50)

        signals: list[Signal] = []

        for symbol, df in market_data.items():
            if len(df) < min_points:
                continue

            closes = df["close"].dropna()
            if len(closes) < bb_period + rsi_period:
                continue

            upper, middle, lower = _bollinger_bands(closes, bb_period, bb_std)
            rsi = _rsi(closes, rsi_period)

            last_close = float(closes.iloc[-1])
            last_upper = float(upper.iloc[-1])
            last_lower = float(lower.iloc[-1])
            last_middle = float(middle.iloc[-1])
            last_rsi = float(rsi.iloc[-1])

            if pd.isna(last_upper) or pd.isna(last_rsi):
                continue

            has_position = symbol in portfolio.positions

            # Oversold: price below lower band AND RSI below threshold → BUY
            if last_close < last_lower and last_rsi < rsi_oversold:
                # Strength = how far below the band (normalized)
                band_width = last_upper - last_lower
                distance = (last_lower - last_close) / band_width if band_width > 0 else 0
                strength = min(1.0, 0.5 + distance)
                signals.append(
                    Signal(
                        source=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.LONG,
                        strength=strength,
                        rationale=(
                            f"Price ({last_close:.2f}) below lower BB ({last_lower:.2f}), "
                            f"RSI={last_rsi:.1f} oversold"
                        ),
                    )
                )

            # Overbought: price above upper band AND RSI above threshold → SELL/FLAT
            elif last_close > last_upper and last_rsi > rsi_overbought:
                strength = min(1.0, 0.5 + (last_close - last_upper) / (last_upper - last_middle + 1e-10))
                direction = SignalDirection.FLAT if not has_position else SignalDirection.SHORT
                signals.append(
                    Signal(
                        source=self.strategy_id,
                        symbol=symbol,
                        direction=direction,
                        strength=strength,
                        rationale=(
                            f"Price ({last_close:.2f}) above upper BB ({last_upper:.2f}), "
                            f"RSI={last_rsi:.1f} overbought"
                        ),
                    )
                )

            # Price reverting back to mean from long → take profit
            elif has_position and abs(last_close - last_middle) / last_middle < 0.005:
                signals.append(
                    Signal(
                        source=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.FLAT,
                        strength=0.6,
                        rationale=f"Price reverted to mean ({last_middle:.2f}). Taking profit.",
                    )
                )

        return signals
