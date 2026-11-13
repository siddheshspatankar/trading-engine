"""
Domain event system for the trading engine.

Events flow through the system asynchronously via an internal event bus.
Every significant state change publishes an event; consumers react without tight coupling.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Coroutine, TypeVar

from pydantic import BaseModel, Field

from src.core.models import Fill, Order, Portfolio, Signal


# ---------------------------------------------------------------------------
# Base Event
# ---------------------------------------------------------------------------


class DomainEvent(BaseModel):
    """Base class for all domain events."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "trading-engine"
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        frozen = True


# ---------------------------------------------------------------------------
# Market Data Events
# ---------------------------------------------------------------------------


class MarketDataReceived(DomainEvent):
    event_type: str = "market_data.received"
    symbol: str
    bar_count: int


class QuoteReceived(DomainEvent):
    event_type: str = "market_data.quote_received"
    symbol: str
    bid: float
    ask: float


# ---------------------------------------------------------------------------
# Strategy / Signal Events
# ---------------------------------------------------------------------------


class SignalGenerated(DomainEvent):
    event_type: str = "strategy.signal_generated"
    signal: Signal


class StrategyStarted(DomainEvent):
    event_type: str = "strategy.started"
    strategy_id: str
    strategy_name: str


class StrategyHalted(DomainEvent):
    event_type: str = "strategy.halted"
    strategy_id: str
    reason: str


# ---------------------------------------------------------------------------
# Risk Events
# ---------------------------------------------------------------------------


class RiskCheckPassed(DomainEvent):
    event_type: str = "risk.check_passed"
    order_id: str
    checks_performed: list[str]


class RiskCheckFailed(DomainEvent):
    event_type: str = "risk.check_failed"
    order_id: str
    reason: str
    limit_type: str


class DrawdownAlertTriggered(DomainEvent):
    event_type: str = "risk.drawdown_alert"
    current_drawdown_pct: float
    threshold_pct: float


class TradingHaltTriggered(DomainEvent):
    event_type: str = "risk.trading_halt"
    reason: str
    daily_loss_pct: float


# ---------------------------------------------------------------------------
# Order Lifecycle Events
# ---------------------------------------------------------------------------


class OrderCreated(DomainEvent):
    event_type: str = "order.created"
    order: Order


class OrderSubmitted(DomainEvent):
    event_type: str = "order.submitted"
    order_id: str
    broker_order_id: str | None = None


class OrderFilled(DomainEvent):
    event_type: str = "order.filled"
    order_id: str
    fill: Fill


class OrderCancelled(DomainEvent):
    event_type: str = "order.cancelled"
    order_id: str
    reason: str | None = None


class OrderRejected(DomainEvent):
    event_type: str = "order.rejected"
    order_id: str
    reason: str


# ---------------------------------------------------------------------------
# Portfolio Events
# ---------------------------------------------------------------------------


class PortfolioUpdated(DomainEvent):
    event_type: str = "portfolio.updated"
    portfolio: Portfolio


class PositionOpened(DomainEvent):
    event_type: str = "portfolio.position_opened"
    symbol: str
    quantity: float
    average_cost: float
    strategy_id: str | None = None


class PositionClosed(DomainEvent):
    event_type: str = "portfolio.position_closed"
    symbol: str
    realized_pnl: float
    strategy_id: str | None = None


# ---------------------------------------------------------------------------
# AI Agent Events
# ---------------------------------------------------------------------------


class AgentAnalysisComplete(DomainEvent):
    event_type: str = "agent.analysis_complete"
    agent_id: str
    agent_type: str
    recommendation: str
    confidence: float
    reasoning: str


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------

EventHandler = Callable[[DomainEvent], Coroutine[Any, Any, None]]
T = TypeVar("T", bound=DomainEvent)


class EventBus:
    """
    In-process async event bus.

    Handlers are registered per event type and called when an event is published.
    For cross-process delivery, events are also forwarded to Kafka.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: type[T], handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        key = event_type.__fields__["event_type"].default
        if key not in self._handlers:
            self._handlers[key] = []
        self._handlers[key].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        """Publish an event to all registered handlers."""
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                # Handlers must not crash the publisher — log and continue
                import structlog
                log = structlog.get_logger()
                log.exception(
                    "event_handler_failed",
                    event_type=event.event_type,
                    event_id=event.event_id,
                )

    def clear(self) -> None:
        """Remove all registered handlers (useful in tests)."""
        self._handlers.clear()


# Global event bus instance
event_bus = EventBus()
