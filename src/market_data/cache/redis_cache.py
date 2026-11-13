"""
Redis-backed market data cache.

Caches OHLCV bars and quotes to reduce API calls and latency.
TTL is set per data type (quotes expire quickly; daily bars persist longer).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pandas as pd
import structlog

from src.core.models import Quote
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)

# Cache TTL constants
QUOTE_TTL = timedelta(seconds=5)
MINUTE_BAR_TTL = timedelta(minutes=5)
DAILY_BAR_TTL = timedelta(hours=24)


class MarketDataCache:
    """
    Redis cache for market data.

    Uses structured keys: md:{provider}:{symbol}:{data_type}:{timeframe}
    """

    def __init__(self) -> None:
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """Lazy-initialize the Redis client."""
        if self._redis is None:
            import redis.asyncio as aioredis  # type: ignore

            settings = get_settings()
            self._redis = aioredis.from_url(
                settings.redis.url.get_secret_value(),
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    @staticmethod
    def _bars_key(provider: str, symbol: str, timeframe: str) -> str:
        return f"md:{provider}:{symbol}:bars:{timeframe}"

    @staticmethod
    def _quote_key(provider: str, symbol: str) -> str:
        return f"md:{provider}:{symbol}:quote"

    async def get_bars(
        self, provider: str, symbol: str, timeframe: str
    ) -> pd.DataFrame | None:
        """Return cached bars DataFrame, or None if cache miss."""
        try:
            redis = await self._get_redis()
            key = self._bars_key(provider, symbol, timeframe)
            data = await redis.get(key)
            if data is None:
                return None
            df = pd.read_json(data, orient="split")
            df.index = pd.to_datetime(df.index, utc=True)
            log.debug("cache_hit_bars", symbol=symbol, timeframe=timeframe)
            return df
        except Exception as exc:
            log.warning("cache_get_bars_failed", symbol=symbol, error=str(exc))
            return None

    async def set_bars(
        self,
        provider: str,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        ttl: timedelta | None = None,
    ) -> None:
        """Cache a bars DataFrame."""
        try:
            redis = await self._get_redis()
            key = self._bars_key(provider, symbol, timeframe)
            ttl = ttl or (DAILY_BAR_TTL if timeframe in ("1D", "1W") else MINUTE_BAR_TTL)
            data = df.to_json(orient="split", date_format="iso")
            await redis.setex(key, int(ttl.total_seconds()), data)
        except Exception as exc:
            log.warning("cache_set_bars_failed", symbol=symbol, error=str(exc))

    async def get_quote(self, provider: str, symbol: str) -> dict[str, Any] | None:
        """Return cached quote dict, or None if cache miss."""
        try:
            redis = await self._get_redis()
            key = self._quote_key(provider, symbol)
            data = await redis.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as exc:
            log.warning("cache_get_quote_failed", symbol=symbol, error=str(exc))
            return None

    async def set_quote(
        self, provider: str, symbol: str, quote: Quote, ttl: timedelta = QUOTE_TTL
    ) -> None:
        """Cache a quote."""
        try:
            redis = await self._get_redis()
            key = self._quote_key(provider, symbol)
            data = quote.model_dump_json()
            await redis.setex(key, int(ttl.total_seconds()), data)
        except Exception as exc:
            log.warning("cache_set_quote_failed", symbol=symbol, error=str(exc))

    async def invalidate(self, pattern: str) -> int:
        """Delete cache keys matching a pattern. Returns count deleted."""
        try:
            redis = await self._get_redis()
            keys = await redis.keys(pattern)
            if keys:
                return await redis.delete(*keys)
            return 0
        except Exception as exc:
            log.warning("cache_invalidate_failed", pattern=pattern, error=str(exc))
            return 0

    async def health_check(self) -> bool:
        """Ping Redis."""
        try:
            redis = await self._get_redis()
            return await redis.ping()
        except Exception:
            return False
