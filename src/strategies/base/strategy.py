"""
Abstract base class for all trading strategies.

A strategy receives market data and produces trading signals.
It does NOT place orders directly — that is the execution engine's responsibility.

Design principles:
- Strategies are stateless in their signal logic (state lives in the portfolio)
- Strategies can be backtested identically to how they run live
- Configuration is explicit and validated at startup
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
import structlog

from src.core.models import Portfolio, Signal
from src.core.exceptions import StrategyConfigError

log = structlog.get_logger(__name__)


class StrategyConfig:
    """Base configuration container for strategies."""

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def validate(self) -> None:
        """Override in subclasses to validate configuration values."""
        pass


class BaseStrategy(ABC):
    """
    Abstract base for all quantitative trading strategies.

    The execute() method is the single entry point called by the engine.
    It receives a snapshot of market data and current portfolio, and returns signals.
    """

    def __init__(self, strategy_id: str, config: dict[str, Any]) -> None:
        self.strategy_id = strategy_id
        self.config = config
        self._is_initialized = False
        self._log = log.bind(strategy_id=strategy_id, strategy=self.name)

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @property
    @abstractmethod
    def universe(self) -> list[str]:
        """List of symbols this strategy trades."""
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """
        Called once before the strategy starts generating signals.
        Load any persistent state, warm up indicators, pre-fetch data.
        """
        ...

    @abstractmethod
    async def generate_signals(
        self,
        market_data: dict[str, pd.DataFrame],
        portfolio: Portfolio,
    ) -> list[Signal]:
        """
        Core signal generation method.

        Args:
            market_data: Dict of symbol -> OHLCV DataFrame (UTC indexed)
            portfolio: Current portfolio snapshot

        Returns:
            List of Signal objects. Empty list = no view / hold.
        """
        ...

    async def execute(
        self,
        market_data: dict[str, pd.DataFrame],
        portfolio: Portfolio,
    ) -> list[Signal]:
        """
        Public entry point. Validates inputs then calls generate_signals().
        """
        if not self._is_initialized:
            await self.initialize()
            self._is_initialized = True

        # Validate required symbols are present
        missing = [sym for sym in self.universe if sym not in market_data]
        if missing:
            self._log.warning("missing_symbols", missing=missing)

        try:
            signals = await self.generate_signals(market_data, portfolio)
            self._log.info(
                "signals_generated",
                count=len(signals),
                symbols=[s.symbol for s in signals],
            )
            return signals
        except Exception as exc:
            self._log.error("signal_generation_failed", error=str(exc))
            raise

    @abstractmethod
    async def teardown(self) -> None:
        """Called when the strategy is stopped. Release resources."""
        ...


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------

_registry: dict[str, type[BaseStrategy]] = {}


def register_strategy(name: str):  # type: ignore
    """Decorator to register a strategy class by name."""
    def decorator(cls: type[BaseStrategy]) -> type[BaseStrategy]:
        _registry[name] = cls
        return cls
    return decorator


def get_strategy_class(name: str) -> type[BaseStrategy]:
    """Look up a strategy class by name."""
    from src.core.exceptions import StrategyNotFound

    if name not in _registry:
        raise StrategyNotFound(
            f"Strategy {name!r} not found. Available: {list(_registry.keys())}"
        )
    return _registry[name]


def list_strategies() -> list[str]:
    """Return all registered strategy names."""
    return list(_registry.keys())
