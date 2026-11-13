"""
Alpaca Markets data provider.

Supports both historical data and real-time streaming via the Alpaca Data API v2.
Used for both market data and as the primary paper/live broker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator

import pandas as pd
import structlog

from src.core.exceptions import DataProviderUnavailable, StaleDataError
from src.core.models import Price, Quote
from src.infrastructure.config import get_settings
from src.market_data.providers.base import MarketDataProvider

log = structlog.get_logger(__name__)


class AlpacaDataProvider(MarketDataProvider):
    """
    Market data provider backed by the Alpaca Data API.

    Authentication is read from AlpacaSettings (ALPACA_API_KEY / ALPACA_SECRET_KEY).
    """

    def __init__(self) -> None:
        self._settings = get_settings().alpaca
        self._client: object | None = None

    @property
    def name(self) -> str:
        return "alpaca"

    def _get_client(self) -> object:
        """Lazy-initialize the Alpaca REST client."""
        if self._client is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient  # type: ignore

                self._client = StockHistoricalDataClient(
                    api_key=self._settings.api_key.get_secret_value(),
                    secret_key=self._settings.secret_key.get_secret_value(),
                )
            except ImportError as exc:
                raise DataProviderUnavailable(
                    "alpaca-py package not installed. Run: pip install alpaca-py"
                ) from exc
        return self._client

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1D",
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch historical bars from Alpaca."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
            from alpaca.data.requests import StockBarsRequest  # type: ignore
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # type: ignore

            client = self._get_client()
            tf_map = {
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1H": TimeFrame.Hour,
                "1D": TimeFrame.Day,
            }
            tf = tf_map.get(timeframe, TimeFrame.Day)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
                adjustment="all",   # corporate action adjusted
            )

            # Run blocking SDK call in thread pool
            bars = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_stock_bars(request).df
            )

            if bars.empty:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

            # Normalize multi-index if multiple symbols were returned
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.xs(symbol, level="symbol")

            bars = bars[["open", "high", "low", "close", "volume"]].copy()
            bars.index = pd.to_datetime(bars.index, utc=True)

            log.debug("alpaca_bars_fetched", symbol=symbol, rows=len(bars))
            return bars

        except Exception as exc:
            log.error("alpaca_data_error", symbol=symbol, error=str(exc))
            raise DataProviderUnavailable(f"Alpaca data fetch failed: {exc}") from exc

    async def get_latest_bar(self, symbol: str) -> Price:
        """Fetch the most recent bar."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
            from alpaca.data.requests import StockLatestBarRequest  # type: ignore

            client = self._get_client()
            request = StockLatestBarRequest(symbol_or_symbols=symbol)
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_stock_latest_bar(request)
            )
            bar = result[symbol]
            return Price(
                symbol=symbol,
                timestamp=bar.timestamp,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=Decimal(str(bar.volume)),
                vwap=Decimal(str(bar.vwap)) if bar.vwap else None,
                trades=bar.trade_count,
            )
        except Exception as exc:
            raise DataProviderUnavailable(f"Alpaca latest bar failed: {exc}") from exc

    async def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch the latest bid/ask quote."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
            from alpaca.data.requests import StockLatestQuoteRequest  # type: ignore

            client = self._get_client()
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_stock_latest_quote(request)
            )
            q = result[symbol]
            return Quote(
                symbol=symbol,
                timestamp=q.timestamp,
                bid=Decimal(str(q.bid_price)),
                ask=Decimal(str(q.ask_price)),
                bid_size=Decimal(str(q.bid_size)),
                ask_size=Decimal(str(q.ask_size)),
            )
        except Exception as exc:
            raise DataProviderUnavailable(f"Alpaca latest quote failed: {exc}") from exc

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Stream real-time quotes via Alpaca WebSocket."""
        try:
            from alpaca.data.live import StockDataStream  # type: ignore

            stream = StockDataStream(
                api_key=self._settings.api_key.get_secret_value(),
                secret_key=self._settings.secret_key.get_secret_value(),
            )
            queue: asyncio.Queue[Quote] = asyncio.Queue()

            async def on_quote(data: object) -> None:  # type: ignore
                quote = Quote(
                    symbol=data.symbol,  # type: ignore
                    timestamp=data.timestamp,  # type: ignore
                    bid=Decimal(str(data.bid_price)),  # type: ignore
                    ask=Decimal(str(data.ask_price)),  # type: ignore
                    bid_size=Decimal(str(data.bid_size)),  # type: ignore
                    ask_size=Decimal(str(data.ask_size)),  # type: ignore
                )
                await queue.put(quote)

            stream.subscribe_quotes(on_quote, *symbols)
            asyncio.create_task(stream.run())

            while True:
                yield await queue.get()

        except Exception as exc:
            raise DataProviderUnavailable(f"Alpaca stream failed: {exc}") from exc

    async def stream_bars(
        self, symbols: list[str], timeframe: str = "1Min"
    ) -> AsyncIterator[Price]:
        """Stream real-time minute bars."""
        try:
            from alpaca.data.live import StockDataStream  # type: ignore

            stream = StockDataStream(
                api_key=self._settings.api_key.get_secret_value(),
                secret_key=self._settings.secret_key.get_secret_value(),
            )
            queue: asyncio.Queue[Price] = asyncio.Queue()

            async def on_bar(data: object) -> None:  # type: ignore
                bar = Price(
                    symbol=data.symbol,  # type: ignore
                    timestamp=data.timestamp,  # type: ignore
                    open=Decimal(str(data.open)),  # type: ignore
                    high=Decimal(str(data.high)),  # type: ignore
                    low=Decimal(str(data.low)),  # type: ignore
                    close=Decimal(str(data.close)),  # type: ignore
                    volume=Decimal(str(data.volume)),  # type: ignore
                )
                await queue.put(bar)

            stream.subscribe_bars(on_bar, *symbols)
            asyncio.create_task(stream.run())

            while True:
                yield await queue.get()

        except Exception as exc:
            raise DataProviderUnavailable(f"Alpaca bar stream failed: {exc}") from exc

    async def health_check(self) -> bool:
        """Return True if Alpaca is reachable."""
        try:
            from alpaca.trading.client import TradingClient  # type: ignore

            client = TradingClient(
                api_key=self._settings.api_key.get_secret_value(),
                secret_key=self._settings.secret_key.get_secret_value(),
                paper=True,
            )
            await asyncio.get_event_loop().run_in_executor(None, client.get_account)
            return True
        except Exception:
            return False
