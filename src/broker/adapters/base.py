"""
Abstract broker adapter interface.

Every broker (Alpaca, Interactive Brokers, paper) implements this interface.
The execution engine only depends on this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from src.core.models import Fill, Order, OrderStatus, Portfolio, Position


class BrokerAdapter(ABC):
    """
    Unified interface for interacting with a broker.

    Implementations handle authentication, order translation, and fill tracking.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable broker name."""
        ...

    @abstractmethod
    async def submit_order(self, order: Order) -> Order:
        """
        Submit an order to the broker.

        Returns the order with broker_order_id populated and status updated.
        Raises BrokerError on failure.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""
        ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """Fetch the current status of an order from the broker."""
        ...

    @abstractmethod
    async def get_fills(self, broker_order_id: str) -> list[Fill]:
        """Fetch all fills for an order."""
        ...

    @abstractmethod
    async def get_account(self) -> Portfolio:
        """Fetch current account state including positions, cash, and equity."""
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None:
        """Fetch the current position for a symbol, or None if flat."""
        ...

    @abstractmethod
    async def get_buying_power(self) -> Decimal:
        """Return current buying power (cash + margin available)."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the broker is reachable and authenticated."""
        ...

    @abstractmethod
    async def close_all_positions(self) -> list[Order]:
        """
        Emergency close of all open positions.
        Use with caution — only for circuit breaker scenarios.
        """
        ...
