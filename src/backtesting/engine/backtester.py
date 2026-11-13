"""
Event-Driven Backtester.

Simulates strategy execution over historical data with:
- Realistic transaction costs (commission + slippage)
- No look-ahead bias (data only available up to current bar)
- Full position and P&L tracking
- Detailed trade log

The backtester uses the same strategy interface as live trading,
ensuring simulation fidelity.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.broker.adapters.paper_broker import PaperBrokerAdapter
from src.core.models import Portfolio, Signal, SignalDirection
from src.risk.engine.risk_engine import RiskEngine
from src.strategies.base.strategy import BaseStrategy

log = structlog.get_logger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    initial_capital: Decimal = Decimal("100000")
    commission_per_share: Decimal = Decimal("0.005")
    slippage_bps: int = 5
    min_commission: Decimal = Decimal("1.00")
    sizing_method: str = "equal_weight"
    rebalance_frequency: str = "daily"  # daily | weekly | monthly
    start_date: datetime | None = None
    end_date: datetime | None = None


@dataclass
class TradeRecord:
    """Single trade record in the backtest."""

    timestamp: datetime
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    pnl: float = 0.0
    strategy: str = ""


@dataclass
class BacktestResult:
    """Full results of a backtest run."""

    strategy_name: str
    config: BacktestConfig
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float

    # Equity curve (daily)
    equity_curve: pd.Series = field(default_factory=pd.Series)

    # Trade log
    trades: list[TradeRecord] = field(default_factory=list)

    # Metrics
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.initial_capital) / self.initial_capital

    @property
    def num_trades(self) -> int:
        return len(self.trades)


class Backtester:
    """
    Event-driven backtester.

    Iterates over historical data bar by bar, feeds each bar to the strategy,
    and simulates execution through the paper broker.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()

    async def run(
        self,
        strategy: BaseStrategy,
        market_data: dict[str, pd.DataFrame],
        lookback_bars: int = 252,
    ) -> BacktestResult:
        """
        Run a full backtest.

        Args:
            strategy: Strategy instance (uses same interface as live trading)
            market_data: Dict of symbol → OHLCV DataFrame (UTC indexed)
            lookback_bars: Number of bars of historical data to give the strategy

        Returns:
            BacktestResult with equity curve, trades, and performance metrics.
        """
        cfg = self._config

        # Align all data on common dates
        all_dates = sorted(
            set.intersection(*[set(df.index) for df in market_data.values()])
        )

        if not all_dates:
            raise ValueError("No common dates across all symbols")

        # Filter to date range
        if cfg.start_date:
            all_dates = [d for d in all_dates if d >= pd.Timestamp(cfg.start_date, tz="UTC")]
        if cfg.end_date:
            all_dates = [d for d in all_dates if d <= pd.Timestamp(cfg.end_date, tz="UTC")]

        if not all_dates:
            raise ValueError("No dates in specified range")

        log.info(
            "backtest_start",
            strategy=strategy.name,
            symbols=list(market_data.keys()),
            start=str(all_dates[0]),
            end=str(all_dates[-1]),
            bars=len(all_dates),
        )

        broker = PaperBrokerAdapter(
            initial_capital=cfg.initial_capital,
            slippage_bps=cfg.slippage_bps,
        )
        risk_engine = RiskEngine()

        equity_curve: list[tuple[datetime, float]] = []
        all_trades: list[TradeRecord] = []

        await strategy.initialize()

        for i, current_date in enumerate(all_dates):
            if i < lookback_bars:
                # Not enough history yet
                portfolio = await broker.get_account()
                equity_curve.append((current_date, float(portfolio.total_equity or cfg.initial_capital)))
                continue

            # Provide only data up to current bar (no look-ahead)
            window_data = {
                sym: df.loc[df.index <= current_date].tail(lookback_bars + 50)
                for sym, df in market_data.items()
            }

            # Update paper broker with current prices
            for sym, df in window_data.items():
                if not df.empty:
                    broker.set_market_price(sym, Decimal(str(df["close"].iloc[-1])))

            portfolio = await broker.get_account()

            # Update position prices in portfolio
            prices_snapshot: dict[str, Decimal] = {
                sym: Decimal(str(df["close"].iloc[-1]))
                for sym, df in window_data.items()
                if not df.empty
            }

            # Generate signals
            try:
                signals = await strategy.execute(window_data, portfolio)
            except Exception as exc:
                log.warning("backtest_signal_error", date=str(current_date), error=str(exc))
                signals = []

            # Execute signals through risk engine and paper broker
            for signal in signals:
                if signal.direction == SignalDirection.FLAT:
                    pos = portfolio.positions.get(signal.symbol)
                    if not pos:
                        continue
                    side = __import__("src.core.models", fromlist=["OrderSide"]).OrderSide.SELL if pos.is_long else \
                        __import__("src.core.models", fromlist=["OrderSide"]).OrderSide.BUY
                    from src.core.models import Order, OrderType, TimeInForce
                    order = Order(
                        symbol=signal.symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        quantity=abs(pos.quantity),
                        time_in_force=TimeInForce.DAY,
                        strategy_id=strategy.strategy_id,
                        signal_id=signal.id,
                    )
                else:
                    price = prices_snapshot.get(signal.symbol)
                    if not price:
                        continue

                    max_pct = Decimal(str(0.10))  # 10% max position
                    target_notional = (portfolio.total_equity or cfg.initial_capital) * max_pct
                    target_notional *= Decimal(str(signal.strength))
                    qty = max(Decimal("1"), (target_notional / price).quantize(Decimal("1")))

                    from src.core.models import Order, OrderSide, OrderType, TimeInForce
                    side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL

                    order = Order(
                        symbol=signal.symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        quantity=qty,
                        time_in_force=TimeInForce.DAY,
                        strategy_id=strategy.strategy_id,
                        signal_id=signal.id,
                    )

                # Risk check
                risk_result = await risk_engine.evaluate_order(order, portfolio, prices_snapshot)
                if not risk_result.passed:
                    continue

                order = order.model_copy(update={"quantity": risk_result.authorized_quantity})
                filled = await broker.submit_order(order)

                if filled.average_fill_price and filled.filled_quantity > 0:
                    all_trades.append(
                        TradeRecord(
                            timestamp=current_date,
                            symbol=filled.symbol,
                            side=filled.side.value,
                            quantity=float(filled.filled_quantity),
                            price=float(filled.average_fill_price),
                            commission=0.0,  # tracked in broker
                            strategy=strategy.name,
                        )
                    )

            # Record equity
            updated_portfolio = await broker.get_account()
            equity_curve.append((current_date, float(updated_portfolio.total_equity or cfg.initial_capital)))

        await strategy.teardown()

        # Build equity series
        equity_series = pd.Series(
            [v for _, v in equity_curve],
            index=pd.DatetimeIndex([d for d, _ in equity_curve]),
        )

        final_equity = float(equity_series.iloc[-1]) if not equity_series.empty else float(cfg.initial_capital)
        metrics = self._compute_metrics(equity_series, all_trades)

        result = BacktestResult(
            strategy_name=strategy.name,
            config=cfg,
            start_date=all_dates[0],
            end_date=all_dates[-1],
            initial_capital=float(cfg.initial_capital),
            final_equity=final_equity,
            equity_curve=equity_series,
            trades=all_trades,
            metrics=metrics,
        )

        log.info(
            "backtest_complete",
            strategy=strategy.name,
            total_return=f"{result.total_return:.2%}",
            sharpe=f"{metrics.get('sharpe_ratio', 0):.2f}",
            max_drawdown=f"{metrics.get('max_drawdown', 0):.2%}",
            num_trades=result.num_trades,
        )
        return result

    def _compute_metrics(
        self, equity_curve: pd.Series, trades: list[TradeRecord]
    ) -> dict[str, float]:
        """Compute standard performance metrics."""
        if equity_curve.empty or len(equity_curve) < 2:
            return {}

        returns = equity_curve.pct_change().dropna()
        total_return = (equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0]
        num_days = max(1, len(equity_curve))
        num_years = num_days / 252

        # Annualized return (CAGR)
        cagr = (1 + float(total_return)) ** (1 / num_years) - 1 if num_years > 0 else 0

        # Sharpe Ratio (assuming 0% risk-free rate)
        daily_std = float(returns.std())
        daily_mean = float(returns.mean())
        sharpe = (daily_mean / daily_std * np.sqrt(252)) if daily_std > 0 else 0

        # Sortino Ratio
        downside_returns = returns[returns < 0]
        downside_std = float(downside_returns.std()) if not downside_returns.empty else 0
        sortino = (daily_mean / downside_std * np.sqrt(252)) if downside_std > 0 else 0

        # Max Drawdown
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min())

        # Win rate
        profitable_trades = [t for t in trades if t.pnl > 0]
        win_rate = len(profitable_trades) / len(trades) if trades else 0

        # Calmar Ratio
        calmar = cagr / abs(max_drawdown) if max_drawdown != 0 else 0

        return {
            "total_return": float(total_return),
            "cagr": cagr,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar,
            "win_rate": win_rate,
            "num_trades": len(trades),
            "volatility_annual": daily_std * np.sqrt(252),
        }
