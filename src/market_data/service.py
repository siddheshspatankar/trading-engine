"""
Market data service used by the API and dashboard.

Source selection:
- If Alpaca credentials are configured, use Alpaca's free IEX feed (snapshots + daily bars).
- Otherwise (or if Alpaca errors), fall back to a deterministic simulated feed so the
  dashboard stays usable. Every response carries a `source` field so the UI can label it.

Results are cached in memory (quotes ~10s, bars ~5min) to stay within API rate limits.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)

QUOTE_TTL_SECONDS = 10
BARS_TTL_SECONDS = 300

# Rough reference prices so simulated data looks plausible for common tickers.
_REFERENCE_PRICES: dict[str, float] = {
    "AAPL": 230.0, "MSFT": 420.0, "GOOGL": 170.0, "AMZN": 190.0, "NVDA": 130.0,
    "META": 560.0, "TSLA": 250.0, "JPM": 210.0, "V": 280.0, "UNH": 520.0,
    "SPY": 560.0, "QQQ": 480.0, "DIA": 420.0, "IWM": 220.0,
}


@dataclass
class QuoteSnapshot:
    symbol: str
    price: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    timestamp: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seed_for(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


class MarketDataService:
    """Fetches quotes and bars with Alpaca → simulated fallback and in-memory caching."""

    def __init__(self) -> None:
        settings = get_settings().alpaca
        self._api_key = settings.api_key.get_secret_value()
        self._secret_key = settings.secret_key.get_secret_value()
        self._client: Any = None
        self._quote_cache: dict[str, tuple[float, QuoteSnapshot]] = {}
        self._bars_cache: dict[tuple[str, int], tuple[float, pd.DataFrame, str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def alpaca_enabled(self) -> bool:
        return bool(self._api_key and self._secret_key)

    async def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        now = time.time()
        fresh: dict[str, QuoteSnapshot] = {}
        missing: list[str] = []
        for sym in symbols:
            cached = self._quote_cache.get(sym)
            if cached and now - cached[0] < QUOTE_TTL_SECONDS:
                fresh[sym] = cached[1]
            else:
                missing.append(sym)

        if missing:
            fetched: dict[str, QuoteSnapshot] = {}
            if self.alpaca_enabled:
                try:
                    fetched = await self._alpaca_snapshots(missing)
                except Exception as exc:  # network, auth, plan limits
                    log.warning("alpaca_snapshot_failed_using_simulated", error=str(exc))
            for sym in missing:
                quote = fetched.get(sym) or self._simulated_quote(sym)
                self._quote_cache[sym] = (now, quote)
                fresh[sym] = quote

        return [fresh[s] for s in symbols]

    async def get_bars(self, symbol: str, days: int = 180) -> tuple[pd.DataFrame, str]:
        """Return (daily OHLCV DataFrame, source)."""
        key = (symbol, days)
        cached = self._bars_cache.get(key)
        if cached and time.time() - cached[0] < BARS_TTL_SECONDS:
            return cached[1], cached[2]

        df: pd.DataFrame | None = None
        source = "simulated"
        if self.alpaca_enabled:
            try:
                df = await self._alpaca_bars(symbol, days)
                if df is not None and not df.empty:
                    source = "alpaca_iex"
                else:
                    df = None
            except Exception as exc:
                log.warning("alpaca_bars_failed_using_simulated", symbol=symbol, error=str(exc))
                df = None

        if df is None:
            df = self._simulated_bars(symbol, days)

        self._bars_cache[key] = (time.time(), df, source)
        return df, source

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        quotes = await self.get_quotes(symbols)
        return {q.symbol: q.price for q in quotes}

    # ------------------------------------------------------------------
    # Alpaca (IEX feed — included with free accounts)
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore

            self._client = StockHistoricalDataClient(
                api_key=self._api_key, secret_key=self._secret_key
            )
        return self._client

    async def _alpaca_snapshots(self, symbols: list[str]) -> dict[str, QuoteSnapshot]:
        from alpaca.data.enums import DataFeed  # type: ignore
        from alpaca.data.requests import StockSnapshotRequest  # type: ignore

        client = self._get_client()
        request = StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
        loop = asyncio.get_running_loop()
        snapshots = await loop.run_in_executor(None, lambda: client.get_stock_snapshot(request))

        result: dict[str, QuoteSnapshot] = {}
        for sym, snap in (snapshots or {}).items():
            if snap is None or snap.latest_trade is None:
                continue
            price = float(snap.latest_trade.price)
            daily = snap.daily_bar
            prev = snap.previous_daily_bar
            prev_close = float(prev.close) if prev else price
            change = price - prev_close
            result[sym] = QuoteSnapshot(
                symbol=sym,
                price=round(price, 2),
                change=round(change, 2),
                change_pct=round((change / prev_close) * 100, 2) if prev_close else 0.0,
                open=float(daily.open) if daily else price,
                high=float(daily.high) if daily else price,
                low=float(daily.low) if daily else price,
                prev_close=round(prev_close, 2),
                volume=float(daily.volume) if daily else 0.0,
                timestamp=snap.latest_trade.timestamp.isoformat(),
                source="alpaca_iex",
            )
        return result

    async def _alpaca_bars(self, symbol: str, days: int) -> pd.DataFrame:
        from alpaca.data.enums import DataFeed  # type: ignore
        from alpaca.data.requests import StockBarsRequest  # type: ignore
        from alpaca.data.timeframe import TimeFrame  # type: ignore

        client = self._get_client()
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=days),
            feed=DataFeed.IEX,
        )
        loop = asyncio.get_running_loop()
        bars = await loop.run_in_executor(None, lambda: client.get_stock_bars(request).df)
        if bars.empty:
            return bars
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        bars = bars[["open", "high", "low", "close", "volume"]].copy()
        bars.index = pd.to_datetime(bars.index, utc=True)
        return bars

    # ------------------------------------------------------------------
    # Simulated feed (deterministic per symbol, gently ticks intraday)
    # ------------------------------------------------------------------

    def _base_price(self, symbol: str) -> float:
        if symbol in _REFERENCE_PRICES:
            return _REFERENCE_PRICES[symbol]
        return 20.0 + (_seed_for(symbol) % 48000) / 100.0  # 20–500

    def _simulated_bars(self, symbol: str, days: int) -> pd.DataFrame:
        end = pd.Timestamp.now(tz="UTC").normalize()
        dates = pd.bdate_range(end=end, periods=max(30, int(days * 252 / 365)), tz="UTC")
        rng = np.random.default_rng(_seed_for(symbol))
        n = len(dates)
        drift = rng.uniform(-0.0002, 0.0008)
        vol = rng.uniform(0.012, 0.025)
        rets = rng.normal(drift, vol, n)
        # Anchor so the last close lands near the reference price
        closes = self._base_price(symbol) * np.exp(np.cumsum(rets) - np.sum(rets))
        opens = np.concatenate([[closes[0]], closes[:-1]]) * (1 + rng.normal(0, vol / 4, n))
        highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        volume = rng.integers(2_000_000, 40_000_000, n).astype(float)
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
            index=dates,
        ).round(2)

    def _simulated_quote(self, symbol: str) -> QuoteSnapshot:
        bars = self._simulated_bars(symbol, 10)
        prev_close = float(bars["close"].iloc[-2])
        last_close = float(bars["close"].iloc[-1])
        # Small deterministic intraday wiggle so the UI visibly updates
        phase = (time.time() / 30.0) + (_seed_for(symbol) % 100)
        price = last_close * (1 + 0.004 * math.sin(phase) + 0.002 * math.sin(phase * 2.7))
        change = price - prev_close
        return QuoteSnapshot(
            symbol=symbol,
            price=round(price, 2),
            change=round(change, 2),
            change_pct=round(change / prev_close * 100, 2),
            open=float(bars["open"].iloc[-1]),
            high=round(max(float(bars["high"].iloc[-1]), price), 2),
            low=round(min(float(bars["low"].iloc[-1]), price), 2),
            prev_close=round(prev_close, 2),
            volume=float(bars["volume"].iloc[-1]),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="simulated",
        )
