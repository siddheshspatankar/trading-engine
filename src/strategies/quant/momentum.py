"""
Cross-sectional Momentum Strategy.

Ranks symbols by trailing N-month returns, goes long top quintile,
short (or flat) bottom quintile. Rebalances weekly or monthly.

Reference: Jegadeesh & Titman (1993) "Returns to Buying Winners and Selling Losers"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.core.models import Portfolio, Signal, SignalDirection
from src.strategies.base.strategy import BaseStrategy, register_strategy

log = structlog.get_logger(__name__)


@register_strategy("momentum")
class MomentumStrategy(BaseStrategy):
    """
    Cross-sectional momentum strategy.

    Configuration keys:
        universe: List of symbols
        lookback_days: Momentum lookback window (default: 252 = 1 year)
        skip_days: Recent days to skip (default: 21 = 1 month, avoids short-term reversal)
        top_n: Number of top-ranked symbols to go long (default: 5)
        bottom_n: Number of bottom-ranked symbols to short (default: 0 — long-only by default)
        min_data_points: Minimum bars required to rank (default: 100)
    """

    @property
    def name(self) -> str:
        return "momentum"

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
        """Rank universe by momentum and emit long/short/flat signals."""
        lookback = self.config.get("lookback_days", 252)
        skip = self.config.get("skip_days", 21)
        top_n = self.config.get("top_n", 5)
        bottom_n = self.config.get("bottom_n", 0)
        min_points = self.config.get("min_data_points", 100)

        scores: dict[str, float] = {}

        for symbol, df in market_data.items():
            if len(df) < min_points:
                log.warning("insufficient_data", symbol=symbol, rows=len(df))
                continue

            closes = df["close"].dropna()
            if len(closes) < lookback:
                continue

            # Momentum = return over [lookback, skip] window
            # Skip most recent `skip` days to avoid short-term reversal
            start_price = closes.iloc[-(lookback)]
            end_price = closes.iloc[-(skip + 1)] if len(closes) > skip else closes.iloc[-1]
            momentum = float((end_price - start_price) / start_price)
            scores[symbol] = momentum

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        total = len(ranked)

        # Normalize strength to [0, 1] using rank percentile
        rank_map = {sym: (total - i) / total for i, (sym, _) in enumerate(ranked)}

        signals: list[Signal] = []
        long_syms = {sym for sym, _ in ranked[:top_n]}
        short_syms = {sym for sym, _ in ranked[-bottom_n:]} if bottom_n > 0 else set()

        # Emit long signals
        for sym in long_syms:
            signals.append(
                Signal(
                    source=self.strategy_id,
                    symbol=sym,
                    direction=SignalDirection.LONG,
                    strength=rank_map[sym],
                    rationale=f"Top momentum rank. Score: {scores[sym]:.4f}",
                )
            )

        # Emit short signals
        for sym in short_syms:
            signals.append(
                Signal(
                    source=self.strategy_id,
                    symbol=sym,
                    direction=SignalDirection.SHORT,
                    strength=1.0 - rank_map[sym],
                    rationale=f"Bottom momentum rank. Score: {scores[sym]:.4f}",
                )
            )

        # Emit flat signals for symbols currently held but not in top/bottom
        held_symbols = set(portfolio.positions.keys())
        for sym in held_symbols - long_syms - short_syms:
            if sym in scores:
                signals.append(
                    Signal(
                        source=self.strategy_id,
                        symbol=sym,
                        direction=SignalDirection.FLAT,
                        strength=0.5,
                        rationale="Dropped from momentum ranking — exit position",
                    )
                )

        return signals
