"""Strategy catalogue, live signal generation and on-demand backtests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

# Import strategy modules so their @register_strategy decorators run
import src.strategies.quant.mean_reversion  # noqa: F401
import src.strategies.quant.momentum  # noqa: F401
import src.strategies.quant.pairs_trading  # noqa: F401
from src.api.state import state
from src.api.validation import parse_symbols
from src.backtesting.engine.backtester import Backtester, BacktestConfig
from src.strategies.base.strategy import BaseStrategy, get_strategy_class, list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])

DEFAULT_SYMBOLS = "AAPL,MSFT,GOOGL,AMZN,NVDA,META"

# Display metadata + how much history each strategy needs
CATALOG: dict[str, dict[str, Any]] = {
    "momentum": {
        "title": "Cross-sectional Momentum",
        "description": "Ranks symbols by 12-month return (skipping the last month) and goes long the leaders.",
        "signal_days": 420,
        "lookback_bars": 252,
        "min_symbols": 2,
    },
    "mean_reversion": {
        "title": "Mean Reversion",
        "description": "Buys when price closes below the lower Bollinger Band with oversold RSI; exits near the mean.",
        "signal_days": 150,
        "lookback_bars": 60,
        "min_symbols": 1,
    },
    "pairs_trading": {
        "title": "Statistical Pairs",
        "description": "Trades the spread of cointegrated pairs (consecutive symbols) when its z-score stretches.",
        "signal_days": 420,
        "lookback_bars": 120,
        "min_symbols": 2,
    },
}


def _require(name: str) -> dict[str, Any]:
    if name not in list_strategies() or name not in CATALOG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Strategy {name!r} not found")
    return CATALOG[name]


def _build(name: str, symbols: list[str]) -> BaseStrategy:
    meta = CATALOG[name]
    if len(symbols) < meta["min_symbols"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{meta['title']} needs at least {meta['min_symbols']} symbols",
        )
    config: dict[str, Any] = {"universe": symbols}
    if name == "momentum":
        config.update(top_n=max(1, min(3, len(symbols) // 2)), min_data_points=100)
    elif name == "pairs_trading":
        config["pairs"] = [symbols[i : i + 2] for i in range(0, len(symbols) - 1, 2)]
    return get_strategy_class(name)(strategy_id=f"{name}_dashboard", config=config)


async def _load_bars(symbols: list[str], days: int) -> tuple[dict, set[str]]:
    results = await asyncio.gather(*(state.market.get_bars(s, days) for s in symbols))
    data = {s: df for s, (df, _) in zip(symbols, results)}
    sources = {src for _, src in results}
    return data, sources


@router.get("/")
async def list_registered_strategies() -> dict:
    return {
        "strategies": [
            {"name": n, **{k: CATALOG[n][k] for k in ("title", "description", "min_symbols")}}
            for n in list_strategies()
            if n in CATALOG
        ]
    }


@router.get("/{strategy_name}")
async def get_strategy(strategy_name: str) -> dict:
    meta = _require(strategy_name)
    return {"name": strategy_name, "status": "registered", "title": meta["title"],
            "description": meta["description"]}


@router.get("/{strategy_name}/signals")
async def get_signals(strategy_name: str, symbols: str = Query(DEFAULT_SYMBOLS)) -> dict:
    """Run the strategy once on current data and return its signals (no orders placed)."""
    meta = _require(strategy_name)
    syms = parse_symbols(symbols)
    strategy = _build(strategy_name, syms)
    data, sources = await _load_bars(syms, meta["signal_days"])
    portfolio = await state.broker.get_account()
    signals = await strategy.execute(data, portfolio)
    return {
        "strategy": strategy_name,
        "data_sources": sorted(sources),
        "signals": [
            {
                "symbol": s.symbol,
                "direction": s.direction.value,
                "strength": round(s.strength, 3),
                "rationale": s.rationale,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in signals
        ],
    }


class BacktestRequest(BaseModel):
    symbols: str = DEFAULT_SYMBOLS
    days: int = Field(730, ge=365, le=1500)
    capital: float = Field(100_000, ge=1_000, le=10_000_000)


@router.post("/{strategy_name}/backtest")
async def run_backtest(strategy_name: str, req: BacktestRequest) -> dict:
    """Bar-by-bar backtest through the same risk engine and paper broker logic."""
    meta = _require(strategy_name)
    syms = parse_symbols(req.symbols)
    if len(syms) > 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Backtests support up to 10 symbols")
    strategy = _build(strategy_name, syms)
    data, sources = await _load_bars(syms, req.days)

    backtester = Backtester(BacktestConfig(initial_capital=Decimal(str(req.capital))))
    try:
        result = await backtester.run(strategy, data, lookback_bars=meta["lookback_bars"])
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    curve = result.equity_curve
    step = max(1, len(curve) // 300)  # keep the payload small
    return {
        "strategy": strategy_name,
        "symbols": syms,
        "data_sources": sorted(sources),
        "start": result.start_date.isoformat(),
        "end": result.end_date.isoformat(),
        "initial_capital": result.initial_capital,
        "final_equity": round(result.final_equity, 2),
        "total_return_pct": round(result.total_return * 100, 2),
        "num_trades": result.num_trades,
        "metrics": {k: round(float(v), 4) for k, v in result.metrics.items()},
        "equity_curve": [
            {"t": ts.isoformat(), "v": round(float(v), 2)} for ts, v in curve.iloc[::step].items()
        ],
        "trades": [
            {"t": t.timestamp.isoformat(), "symbol": t.symbol, "side": t.side,
             "qty": t.quantity, "price": round(t.price, 2)}
            for t in result.trades[-50:]
        ],
    }
