"""
Statistical Pairs Trading Strategy.

Identifies cointegrated pairs, computes a hedge ratio via OLS regression,
and trades the spread when it deviates significantly from its mean.

Methodology:
1. Test for cointegration using Engle-Granger test
2. Estimate hedge ratio via OLS: log(P_A) = β * log(P_B) + ε
3. Compute Z-score of the spread
4. Enter when |Z| > entry_threshold, exit when |Z| < exit_threshold

Reference: Vidyamurthy (2004) "Pairs Trading: Quantitative Methods and Analysis"
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import coint

from src.core.models import Portfolio, Signal, SignalDirection
from src.strategies.base.strategy import BaseStrategy, register_strategy

log = structlog.get_logger(__name__)


def _compute_spread(
    log_price_a: pd.Series,
    log_price_b: pd.Series,
    lookback: int = 252,
) -> tuple[pd.Series, float, float]:
    """
    Estimate hedge ratio and compute normalized spread (Z-score).

    Returns:
        z_scores: Time series of Z-scores
        hedge_ratio: OLS beta coefficient
        p_value: Cointegration p-value
    """
    # Cointegration test
    _, p_value, _ = coint(log_price_a, log_price_b)

    # Estimate hedge ratio on lookback window
    window_a = log_price_a.iloc[-lookback:]
    window_b = log_price_b.iloc[-lookback:]

    model = OLS(window_a, add_constant(window_b)).fit()
    hedge_ratio = float(model.params.iloc[1])

    # Compute spread
    spread = log_price_a - hedge_ratio * log_price_b
    z_scores = (spread - spread.mean()) / spread.std()

    return z_scores, hedge_ratio, float(p_value)


@register_strategy("pairs_trading")
class PairsTradingStrategy(BaseStrategy):
    """
    Statistical pairs trading strategy.

    Configuration keys:
        pairs: List of [symbol_a, symbol_b] tuples
        entry_z: Z-score threshold to enter trade (default: 2.0)
        exit_z: Z-score threshold to exit trade (default: 0.5)
        stop_z: Z-score stop loss (default: 3.5)
        lookback_days: Window for hedge ratio estimation (default: 252)
        max_p_value: Maximum cointegration p-value to trade a pair (default: 0.05)
        min_data_points: Minimum bars required (default: 100)
    """

    @property
    def name(self) -> str:
        return "pairs_trading"

    @property
    def universe(self) -> list[str]:
        pairs = self.config.get("pairs", [])
        return list({sym for pair in pairs for sym in pair})

    async def initialize(self) -> None:
        self._log.info("strategy_initialized", pairs=self.config.get("pairs", []))

    async def teardown(self) -> None:
        self._log.info("strategy_stopped")

    async def generate_signals(
        self,
        market_data: dict[str, pd.DataFrame],
        portfolio: Portfolio,
    ) -> list[Signal]:
        """Generate pairs trading signals based on spread Z-scores."""
        pairs = self.config.get("pairs", [])
        entry_z = self.config.get("entry_z", 2.0)
        exit_z = self.config.get("exit_z", 0.5)
        stop_z = self.config.get("stop_z", 3.5)
        lookback = self.config.get("lookback_days", 252)
        max_p_value = self.config.get("max_p_value", 0.05)
        min_points = self.config.get("min_data_points", 100)

        signals: list[Signal] = []

        for pair in pairs:
            if len(pair) != 2:
                continue
            sym_a, sym_b = pair[0], pair[1]

            if sym_a not in market_data or sym_b not in market_data:
                continue

            closes_a = market_data[sym_a]["close"].dropna()
            closes_b = market_data[sym_b]["close"].dropna()

            # Align on common timestamps
            common = closes_a.index.intersection(closes_b.index)
            if len(common) < min_points:
                continue

            closes_a = closes_a.loc[common]
            closes_b = closes_b.loc[common]

            log_a = np.log(closes_a)
            log_b = np.log(closes_b)

            try:
                z_scores, hedge_ratio, p_value = _compute_spread(log_a, log_b, lookback)
            except Exception as exc:
                log.warning("pairs_spread_error", pair=pair, error=str(exc))
                continue

            if p_value > max_p_value:
                log.debug(
                    "pair_not_cointegrated",
                    pair=pair,
                    p_value=p_value,
                    threshold=max_p_value,
                )
                continue

            last_z = float(z_scores.iloc[-1])
            in_position_a = sym_a in portfolio.positions
            in_position_b = sym_b in portfolio.positions

            rationale_base = (
                f"Pair ({sym_a}/{sym_b}) | Z={last_z:.2f} | "
                f"HR={hedge_ratio:.4f} | p={p_value:.4f}"
            )

            # Stop loss: Z-score too extreme
            if abs(last_z) > stop_z and (in_position_a or in_position_b):
                signals.extend([
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_a,
                        direction=SignalDirection.FLAT,
                        strength=1.0,
                        rationale=f"STOP LOSS. {rationale_base}",
                    ),
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_b,
                        direction=SignalDirection.FLAT,
                        strength=1.0,
                        rationale=f"STOP LOSS. {rationale_base}",
                    ),
                ])

            # Exit: Z-score reverted to near zero
            elif abs(last_z) < exit_z and (in_position_a or in_position_b):
                signals.extend([
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_a,
                        direction=SignalDirection.FLAT,
                        strength=0.8,
                        rationale=f"Exit: spread reverted. {rationale_base}",
                    ),
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_b,
                        direction=SignalDirection.FLAT,
                        strength=0.8,
                        rationale=f"Exit: spread reverted. {rationale_base}",
                    ),
                ])

            # Entry: Z-score above threshold (A is overpriced relative to B)
            elif last_z > entry_z and not in_position_a:
                signals.extend([
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_a,
                        direction=SignalDirection.SHORT,  # sell overpriced leg
                        strength=min(1.0, last_z / (entry_z + 1)),
                        rationale=f"Short overpriced leg. {rationale_base}",
                    ),
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_b,
                        direction=SignalDirection.LONG,   # buy underpriced leg
                        strength=min(1.0, last_z / (entry_z + 1)),
                        rationale=f"Long underpriced leg. {rationale_base}",
                    ),
                ])

            # Entry: Z-score below threshold (A is underpriced relative to B)
            elif last_z < -entry_z and not in_position_a:
                signals.extend([
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_a,
                        direction=SignalDirection.LONG,
                        strength=min(1.0, abs(last_z) / (entry_z + 1)),
                        rationale=f"Long underpriced leg. {rationale_base}",
                    ),
                    Signal(
                        source=self.strategy_id,
                        symbol=sym_b,
                        direction=SignalDirection.SHORT,
                        strength=min(1.0, abs(last_z) / (entry_z + 1)),
                        rationale=f"Short overpriced leg. {rationale_base}",
                    ),
                ])

        return signals
