"""
Yahoo Finance data provider.

Used for free historical data, backtesting, and research.
Not suitable for real-time production trading (rate limits, no streaming).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator

import pandas as pd
import structlog

from src.core.exceptions import DataProviderUnavailable, InsufficientHistoricalData
from src.core.models import Price, Quote
from src.market_data.providers.base import MarketDataProvider

log = structlog.get_logger(__name__)


class YahooFinanceProvider(MarketDataProvider):
    """
    Market data provider backed by yfinance.

    Good for research and backtesting.
    Does NOT support real-time streaming — raises NotImplementedError.
    """

    @property
    def name(self) -> str:
        return "yahoo_finance"

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1D",
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch historical bars from Yahoo Finance."""
        try:
            import yfinance as yf  # type: ignore

            tf_map = {
                "1Min": "1m",
                "5Min": "5m",
                "15Min": "15m",
                "30Min": "30m",
                "1H": "1h",
                "1D": "1d",
                "1W": "1wk",
                "1M": "1mo",
            }
            interval = tf_map.get(timeframe, "1d")

            ticker = yf.Ticker(symbol)
            df = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ticker.history(
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval=interval,
                    auto_adjust=True,
                ),
            )

            if df.empty:
                raise InsufficientHistoricalData(
                    f"No data returned from Yahoo Finance for {symbol}"
                )

            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].copy()
            df.index = pd.to_datetime(df.index, utc=True)

            if limit:
                df = df.tail(limit)

            log.debug("yahoo_bars_fetched", symbol=symbol, rows=len(df))
            return df

        except InsufficientHistoricalData:
            raise
        except Exception as exc:
            log.error("yahoo_data_error", symbol=symbol, error=str(exc))
            raise DataProviderUnavailable(f"Yahoo Finance fetch failed: {exc}") from exc

    async def get_latest_bar(self, symbol: str) -> Price:
        """Fetch the most recent bar from Yahoo Finance."""
        try:
            import yfinance as yf  # type: ignore

            ticker = yf.Ticker(symbol)
            df = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ticker.history(period="2d", interval="1d")
            )
            if df.empty:
                raise DataProviderUnavailable(f"No data for {symbol}")

            row = df.iloc[-1]
            return Price(
                symbol=symbol,
                timestamp=df.index[-1].to_pydatetime(),
                open=Decimal(str(row["Open"])),
                high=Decimal(str(row["High"])),
                low=Decimal(str(row["Low"])),
                close=Decimal(str(row["Close"])),
                volume=Decimal(str(row["Volume"])),
            )
        except DataProviderUnavailable:
            raise
        except Exception as exc:
            raise DataProviderUnavailable(f"Yahoo latest bar failed: {exc}") from exc

    async def get_latest_quote(self, symbol: str) -> Quote:
        """Yahoo Finance does not provide real-time quotes — raises NotImplementedError."""
        raise NotImplementedError(
            "Yahoo Finance does not support real-time quotes. Use Alpaca or Polygon."
        )

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Not supported by Yahoo Finance."""
        raise NotImplementedError("Yahoo Finance does not support quote streaming.")
        # Make this a generator to satisfy the type signature
        return  # type: ignore
        yield  # type: ignore  # noqa: unreachable

    async def stream_bars(
        self, symbols: list[str], timeframe: str = "1Min"
    ) -> AsyncIterator[Price]:
        """Not supported by Yahoo Finance."""
        raise NotImplementedError("Yahoo Finance does not support bar streaming.")
        return  # type: ignore
        yield  # type: ignore  # noqa: unreachable

    async def health_check(self) -> bool:
        """Check Yahoo Finance availability by fetching SPY."""
        try:
            await self.get_latest_bar("SPY")
            return True
        except Exception:
            return False

    async def get_multiple_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1D",
    ) -> dict[str, pd.DataFrame]:
        """Optimized batch fetch using yfinance's multi-symbol download."""
        try:
            import yfinance as yf  # type: ignore

            tf_map = {"1D": "1d", "1H": "1h", "1W": "1wk", "1M": "1mo"}
            interval = tf_map.get(timeframe, "1d")

            df = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: yf.download(
                    tickers=" ".join(symbols),
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                ),
            )

            result: dict[str, pd.DataFrame] = {}
            for sym in symbols:
                try:
                    sym_df = df[sym].copy() if len(symbols) > 1 else df.copy()
                    sym_df.columns = [c.lower() for c in sym_df.columns]
                    sym_df = sym_df[["open", "high", "low", "close", "volume"]].dropna()
                    sym_df.index = pd.to_datetime(sym_df.index, utc=True)
                    result[sym] = sym_df
                except KeyError:
                    log.warning("yahoo_symbol_missing", symbol=sym)

            return result
        except Exception as exc:
            log.error("yahoo_batch_error", symbols=symbols, error=str(exc))
            raise DataProviderUnavailable(f"Yahoo batch fetch failed: {exc}") from exc
