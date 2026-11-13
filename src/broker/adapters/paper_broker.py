"""
Paper Trading Broker Adapter.

Simulates order execution locally without any broker connectivity.
Used for simulation mode and testing. Tracks fills, positions, and P&L
using realistic market impact and slippage models.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from src.broker.adapters.base import BrokerAdapter
from src.core.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    Portfolio,
    Position,
)
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)


class PaperBrokerAdapter(BrokerAdapter):
    """
    Local paper trading broker for simulation and development.

    Applies a configurable slippage model to simulate realistic fills.
    State is in-memory — does not persist across restarts.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("100000"),
        slippage_bps: int | None = None,
    ) -> None:
        settings = get_settings()
        self._cash = initial_capital
        self._slippage_bps = slippage_bps or settings.execution.slippage_bps
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._fills: dict[str, list[Fill]] = {}
        self._market_prices: dict[str, Decimal] = {}
        self._total_commission = Decimal("0")

    @property
    def name(self) -> str:
        return "paper"

    def set_market_price(self, symbol: str, price: Decimal) -> None:
        """Update the simulated market price for a symbol."""
        self._market_prices[symbol] = price

    def mark_to_market(self, prices: dict[str, Decimal]) -> None:
        """Revalue open positions at the latest prices (updates unrealized P&L)."""
        for symbol, price in prices.items():
            self._market_prices[symbol] = price
            pos = self._positions.get(symbol)
            if pos is not None:
                self._positions[symbol] = pos.model_copy(update={"current_price": price})

    def list_orders(self) -> list[Order]:
        """All orders seen by this broker, newest first."""
        return sorted(self._orders.values(), key=lambda o: o.created_at, reverse=True)

    @property
    def total_commission(self) -> Decimal:
        return self._total_commission

    def _apply_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        """Apply slippage: adds to buy price, subtracts from sell price."""
        slippage = price * Decimal(str(self._slippage_bps)) / Decimal("10000")
        return price + slippage if side == OrderSide.BUY else price - slippage

    def _calculate_commission(self, quantity: Decimal, price: Decimal) -> Decimal:
        """Simple flat-rate commission model: $0.005/share, min $1."""
        commission = quantity * Decimal("0.005")
        return max(commission, Decimal("1.00"))

    async def submit_order(self, order: Order) -> Order:
        """Simulate immediate fill at market price with slippage."""
        market_price = self._market_prices.get(order.symbol)
        if market_price is None:
            # Cannot fill without a price — reject
            updated = order.model_copy(update={"status": OrderStatus.REJECTED})
            self._orders[order.id] = updated
            log.warning("paper_order_rejected_no_price", symbol=order.symbol)
            return updated

        # Determine fill price
        if order.limit_price:
            # Simple limit simulation: fill if price is at or better than limit
            if order.side == OrderSide.BUY and market_price > order.limit_price:
                updated = order.model_copy(update={"status": OrderStatus.ACCEPTED})
                self._orders[order.id] = updated
                return updated  # Order resting, not yet filled
            elif order.side == OrderSide.SELL and market_price < order.limit_price:
                updated = order.model_copy(update={"status": OrderStatus.ACCEPTED})
                self._orders[order.id] = updated
                return updated

        fill_price = self._apply_slippage(market_price, order.side)
        commission = self._calculate_commission(order.quantity, fill_price)
        total_cost = order.quantity * fill_price + (commission if order.side == OrderSide.BUY else -commission)

        # Check if we have enough cash for a buy
        if order.side == OrderSide.BUY and total_cost > self._cash:
            # Partial fill to fit within available cash
            max_qty = (self._cash - commission) / fill_price
            if max_qty < Decimal("1"):
                updated = order.model_copy(update={"status": OrderStatus.REJECTED})
                self._orders[order.id] = updated
                log.warning("paper_order_rejected_insufficient_funds", symbol=order.symbol)
                return updated
            fill_qty = max_qty.quantize(Decimal("1"))
        else:
            fill_qty = order.quantity

        # Record fill
        fill = Fill(
            order_id=order.id,
            timestamp=datetime.utcnow(),
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=fill_price,
            commission=commission,
            broker_fill_id=str(uuid.uuid4()),
        )

        if order.id not in self._fills:
            self._fills[order.id] = []
        self._fills[order.id].append(fill)

        # Update cash
        if order.side == OrderSide.BUY:
            self._cash -= fill_qty * fill_price + commission
        else:
            self._cash += fill_qty * fill_price - commission
        self._total_commission += commission

        # Update position
        self._update_position(order.symbol, fill_qty, fill_price, order.side)

        updated = order.model_copy(
            update={
                "status": OrderStatus.FILLED if fill_qty >= order.quantity else OrderStatus.PARTIALLY_FILLED,
                "filled_quantity": fill_qty,
                "average_fill_price": fill_price,
                "broker_order_id": str(uuid.uuid4()),
            }
        )
        self._orders[order.id] = updated

        log.info(
            "paper_order_filled",
            symbol=order.symbol,
            side=order.side.value,
            quantity=float(fill_qty),
            price=float(fill_price),
            commission=float(commission),
        )
        return updated

    def _update_position(
        self,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        side: OrderSide,
    ) -> None:
        """Update internal position tracking."""
        signed_qty = qty if side == OrderSide.BUY else -qty
        existing = self._positions.get(symbol)

        if existing is None:
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=signed_qty,
                average_cost=price,
                current_price=price,
                opened_at=datetime.utcnow(),
            )
        else:
            new_qty = existing.quantity + signed_qty
            if new_qty == 0:
                del self._positions[symbol]
            else:
                # Weighted average cost (only for same-direction adds)
                if (existing.quantity > 0 and signed_qty > 0) or (existing.quantity < 0 and signed_qty < 0):
                    total_cost = existing.quantity * existing.average_cost + signed_qty * price
                    new_avg_cost = total_cost / new_qty
                else:
                    new_avg_cost = existing.average_cost  # Partial close — cost unchanged
                self._positions[symbol] = existing.model_copy(
                    update={"quantity": new_qty, "average_cost": new_avg_cost, "current_price": price}
                )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        order = self._orders.get(order_id)
        if order and order.status in {OrderStatus.PENDING, OrderStatus.ACCEPTED}:
            self._orders[order_id] = order.model_copy(update={"status": OrderStatus.CANCELLED})
            return True
        return False

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        order = self._orders.get(broker_order_id)
        return order.status if order else OrderStatus.REJECTED

    async def get_fills(self, broker_order_id: str) -> list[Fill]:
        return self._fills.get(broker_order_id, [])

    async def get_account(self) -> Portfolio:
        """Return current simulated portfolio state."""
        total_market_value = sum(
            pos.quantity * (pos.current_price or pos.average_cost)
            for pos in self._positions.values()
        )
        total_equity = self._cash + total_market_value

        return Portfolio(
            cash=self._cash,
            total_equity=total_equity,
            positions=dict(self._positions),
        )

    async def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    async def get_buying_power(self) -> Decimal:
        return self._cash

    async def health_check(self) -> bool:
        return True  # Paper broker is always "healthy"

    async def close_all_positions(self) -> list[Order]:
        """Close all positions at current market prices."""
        orders_created = []
        for symbol, pos in list(self._positions.items()):
            price = self._market_prices.get(symbol, pos.current_price or pos.average_cost)
            side = OrderSide.SELL if pos.is_long else OrderSide.BUY
            order = Order(
                symbol=symbol,
                side=side,
                order_type=__import__("src.core.models", fromlist=["OrderType"]).OrderType.MARKET,
                quantity=abs(pos.quantity),
            )
            self.set_market_price(symbol, price or Decimal("1"))
            filled = await self.submit_order(order)
            orders_created.append(filled)
        log.warning("paper_emergency_close_all_positions", count=len(orders_created))
        return orders_created
