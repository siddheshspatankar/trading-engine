"""
Order Execution Engine.

Receives risk-authorized signals, converts them to orders,
applies execution algorithms (TWAP/VWAP/market), and routes to the broker.

Responsibilities:
- Signal → Order translation
- Position sizing from portfolio targets
- Execution algorithm selection
- Order lifecycle tracking
- Fill reconciliation
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog

from src.broker.adapters.base import BrokerAdapter
from src.core.events import OrderCreated, OrderFilled, OrderRejected, event_bus
from src.core.models import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Signal,
    SignalDirection,
    TimeInForce,
)
from src.infrastructure.config import get_settings
from src.risk.engine.risk_engine import RiskEngine

log = structlog.get_logger(__name__)


class PositionSizer:
    """
    Converts signals into position sizes based on portfolio state.

    Supported methods:
    - equal_weight: Equal allocation across all long signals
    - signal_weight: Weight proportional to signal strength
    - fixed_notional: Fixed dollar amount per signal
    - kelly: Kelly criterion (capped at max_position_pct)
    """

    def __init__(self, method: str = "equal_weight") -> None:
        self._method = method
        self._settings = get_settings()

    def size_positions(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        market_prices: dict[str, Decimal],
    ) -> dict[str, Decimal]:
        """
        Return a dict of {symbol: target_quantity} for all actionable signals.

        Positive = long, negative = short, 0 = exit.
        """
        if portfolio.total_equity is None or portfolio.total_equity == 0:
            return {}

        # Separate signals by direction
        long_signals = [s for s in signals if s.direction == SignalDirection.LONG]
        short_signals = [s for s in signals if s.direction == SignalDirection.SHORT]
        exit_signals = [s for s in signals if s.direction == SignalDirection.FLAT]

        result: dict[str, Decimal] = {}

        # Exit signals → zero quantity
        for sig in exit_signals:
            result[sig.symbol] = Decimal("0")

        # Size long positions
        if long_signals:
            long_sizes = self._compute_sizes(
                long_signals, portfolio, market_prices, is_long=True
            )
            result.update(long_sizes)

        # Size short positions
        if short_signals:
            short_sizes = self._compute_sizes(
                short_signals, portfolio, market_prices, is_long=False
            )
            result.update(short_sizes)

        return result

    def _compute_sizes(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        market_prices: dict[str, Decimal],
        is_long: bool,
    ) -> dict[str, Decimal]:
        """Compute target quantities for a set of directional signals."""
        total_equity = portfolio.total_equity or Decimal("1")
        max_pct = Decimal(str(self._settings.risk.max_position_pct))
        result = {}

        if self._method == "equal_weight":
            n = len(signals)
            # Allocate equally but cap at max_position_pct
            weight = min(Decimal("1") / Decimal(str(n)), max_pct)

        elif self._method == "signal_weight":
            total_strength = sum(s.strength for s in signals) or 1.0
            weights = {
                s.symbol: Decimal(str(s.strength / total_strength)) for s in signals
            }

        for sig in signals:
            price = market_prices.get(sig.symbol)
            if not price or price == 0:
                continue

            if self._method == "equal_weight":
                notional = total_equity * weight
            elif self._method == "signal_weight":
                notional = total_equity * weights[sig.symbol]  # type: ignore
            else:
                notional = total_equity * max_pct

            # Apply signal strength as a scalar to notional
            notional *= Decimal(str(sig.strength))
            qty = (notional / price).quantize(Decimal("1"))  # whole shares

            if qty > 0:
                result[sig.symbol] = qty if is_long else -qty

        return result


class ExecutionEngine:
    """
    Converts risk-authorized signals into broker orders.

    Applies position sizing, generates orders, routes to the broker,
    and tracks the full order lifecycle.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        risk_engine: RiskEngine,
        sizing_method: str = "equal_weight",
    ) -> None:
        self._broker = broker
        self._risk_engine = risk_engine
        self._sizer = PositionSizer(method=sizing_method)
        self._pending_orders: dict[str, Order] = {}
        self._settings = get_settings()

    async def execute_signals(
        self,
        signals: list[Signal],
        portfolio: Portfolio,
        market_prices: dict[str, Decimal],
    ) -> list[Order]:
        """
        Process a list of signals and execute orders.

        Flow:
        1. Convert signals to target quantities
        2. Compute order quantities (target - current position)
        3. Run risk authorization for each order
        4. Submit authorized orders to broker
        5. Track fills

        Returns list of submitted (or rejected) orders.
        """
        if not signals:
            return []

        log.info("execution_cycle_start", signal_count=len(signals))

        # Compute target quantities
        targets = self._sizer.size_positions(signals, portfolio, market_prices)
        if not targets:
            log.info("no_actionable_targets")
            return []

        # Build orders from targets
        orders = []
        for symbol, target_qty in targets.items():
            current_pos = portfolio.positions.get(symbol)
            current_qty = current_pos.quantity if current_pos else Decimal("0")
            trade_qty = target_qty - current_qty

            if abs(trade_qty) < Decimal("1"):
                continue  # No meaningful trade needed

            side = OrderSide.BUY if trade_qty > 0 else OrderSide.SELL
            settings = get_settings()
            order_type = OrderType.LIMIT if settings.execution.default_order_type == "LIMIT" else OrderType.MARKET
            limit_price = market_prices.get(symbol)

            # For limit orders, place slightly inside the spread
            if order_type == OrderType.LIMIT and limit_price:
                offset = limit_price * Decimal("0.001")  # 0.1% inside
                limit_price = limit_price + offset if side == OrderSide.BUY else limit_price - offset

            order = Order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=abs(trade_qty),
                limit_price=limit_price if order_type == OrderType.LIMIT else None,
                time_in_force=TimeInForce.DAY,
                strategy_id=signals[0].source if signals else None,
            )
            orders.append(order)
            await event_bus.publish(OrderCreated(order=order))

        # Run risk engine on each order
        authorized_orders = []
        for order in orders:
            risk_result = await self._risk_engine.evaluate_order(
                order, portfolio, market_prices
            )

            if not risk_result.passed:
                log.warning(
                    "order_rejected_by_risk",
                    order_id=order.id,
                    symbol=order.symbol,
                    reason=risk_result.failure_reason,
                )
                await event_bus.publish(
                    OrderRejected(order_id=order.id, reason=risk_result.failure_reason or "Risk check failed")
                )
                continue

            # Apply quantity adjustment from risk engine
            if risk_result.authorized_quantity != order.quantity:
                order = order.model_copy(
                    update={"quantity": risk_result.authorized_quantity}
                )

            authorized_orders.append(order)

        # Submit authorized orders to broker
        submitted_orders = []
        for order in authorized_orders:
            if get_settings().env == "shadow":
                # Shadow mode: log the order but do NOT submit to broker
                log.info(
                    "shadow_mode_order_logged_not_submitted",
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=float(order.quantity),
                )
                self._pending_orders[order.id] = order
                submitted_orders.append(order)
                continue

            try:
                filled_order = await self._broker.submit_order(order)
                self._pending_orders[filled_order.id] = filled_order
                submitted_orders.append(filled_order)

                if filled_order.status == OrderStatus.FILLED:
                    fills = await self._broker.get_fills(filled_order.broker_order_id or filled_order.id)
                    for fill in fills:
                        await event_bus.publish(OrderFilled(order_id=filled_order.id, fill=fill))

            except Exception as exc:
                log.error(
                    "order_submission_failed",
                    order_id=order.id,
                    symbol=order.symbol,
                    error=str(exc),
                )
                await event_bus.publish(
                    OrderRejected(order_id=order.id, reason=str(exc))
                )

        log.info(
            "execution_cycle_complete",
            total_signals=len(signals),
            orders_created=len(orders),
            orders_authorized=len(authorized_orders),
            orders_submitted=len(submitted_orders),
        )
        return submitted_orders

    async def cancel_all_pending(self) -> int:
        """Cancel all tracked pending orders."""
        cancelled = 0
        for order_id, order in list(self._pending_orders.items()):
            if order.broker_order_id and not order.is_terminal:
                success = await self._broker.cancel_order(order.broker_order_id)
                if success:
                    cancelled += 1
        log.info("cancelled_pending_orders", count=cancelled)
        return cancelled
