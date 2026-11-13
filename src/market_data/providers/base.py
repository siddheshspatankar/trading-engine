"""
Abstract base class for all market data providers.

Every provider — Alpaca, Polygon, Yahoo Finance, IB — implements this interface.
The rest of the system only depends on this abstraction, never on a concrete provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator

import pandas as pd

from src.core.models import Price, Quote


class MarketDataProvider(ABC):
    """
    Unified interface for fetching market data.

    Implementations handle authentication, rate limiting, and data normalization.
    All returned data is normalized to the platform's canonical schema.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1D",
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV bars.

        Returns a DataFrame with columns: open, high, low, close, volume.
        Index is a timezone-aware DatetimeIndex (UTC).
        """
        ...

    @abstractmethod
    async def get_latest_bar(self, symbol: str) -> Price:
        """Fetch the most recent completed bar for a symbol."""
        ...

    @abstractmethod
    async def get_latest_quote(self, symbol: str) -> Quote:
        """Fetch the latest bid/ask quote."""
        ...

    @abstractmethod
    async def stream_quotes(
        self, symbols: list[str]
    ) -> AsyncIterator[Quote]:
        """
        Stream real-time quotes for a list of symbols.

        This is an async generator that yields Quote objects as they arrive.
        Callers are responsible for cancelling the iterator when done.
        """
        ...

    @abstractmethod
    async def stream_bars(
        self, symbols: list[str], timeframe: str = "1Min"
    ) -> AsyncIterator[Price]:
        """Stream real-time bars."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and authenticated."""
        ...

    async def get_multiple_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1D",
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch historical bars for multiple symbols.

        Default implementation calls get_bars concurrently.
        Override for providers that support batch endpoints.
        """
        import asyncio

        tasks = {sym: self.get_bars(sym, start, end, timeframe) for sym in symbols}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            sym: result
            for sym, result in zip(tasks.keys(), results)
            if not isinstance(result, Exception)
        }
