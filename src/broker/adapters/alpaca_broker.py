"""
Alpaca Markets broker adapter.

Supports paper and live trading via the Alpaca Trading API v2.
Handles order submission, status polling, and fill tracking.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog

from src.broker.adapters.base import BrokerAdapter
from src.core.exceptions import (
    BrokerAuthenticationError,
    BrokerConnectionError,
    InvalidOrderError,
    OrderRejectedError,
)
from src.core.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    TimeInForce,
)
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)

# Map platform order types to Alpaca types
_ORDER_TYPE_MAP = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
    OrderType.TRAILING_STOP: "trailing_stop",
}

_TIF_MAP = {
    TimeInForce.DAY: "day",
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.GTX: "gtx",
    TimeInForce.OPG: "opg",
    TimeInForce.CLS: "cls",
}

_STATUS_MAP = {
    "new": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.CANCELLED,
    "pending_cancel": OrderStatus.SUBMITTED,
    "pending_replace": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.PENDING,
    "accepted_for_bidding": OrderStatus.ACCEPTED,
    "stopped": OrderStatus.REJECTED,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.REJECTED,
    "calculated": OrderStatus.ACCEPTED,
}


class AlpacaBrokerAdapter(BrokerAdapter):
    """Broker adapter for Alpaca Markets (paper and live)."""

    def __init__(self, paper: bool = True) -> None:
        self._settings = get_settings().alpaca
        self._paper = paper
        self._client: Any = None

    @property
    def name(self) -> str:
        return f"alpaca_{'paper' if self._paper else 'live'}"

    def _get_client(self) -> Any:
        """Lazy-initialize the Alpaca Trading client."""
        if self._client is None:
            try:
                from alpaca.trading.client import TradingClient  # type: ignore

                self._client = TradingClient(
                    api_key=self._settings.api_key.get_secret_value(),
                    secret_key=self._settings.secret_key.get_secret_value(),
                    paper=self._paper,
                )
            except ImportError as exc:
                raise BrokerConnectionError("alpaca-py not installed") from exc
        return self._client

    async def submit_order(self, order: Order) -> Order:
        """Submit order to Alpaca and return updated order object."""
        try:
            from alpaca.trading.requests import (  # type: ignore
                LimitOrderRequest,
                MarketOrderRequest,
                StopLimitOrderRequest,
                StopOrderRequest,
            )
            from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce as AlpacaTIF  # type: ignore

            client = self._get_client()
            side = AlpacaSide.BUY if order.side == OrderSide.BUY else AlpacaSide.SELL
            tif = AlpacaTIF((_TIF_MAP.get(order.time_in_force, "day")))

            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=float(order.quantity),
                    side=side,
                    time_in_force=tif,
                )
            elif order.order_type == OrderType.LIMIT:
                if not order.limit_price:
                    raise InvalidOrderError("Limit order requires a limit price", order_id=order.id)  # type: ignore
                request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=float(order.quantity),
                    side=side,
                    limit_price=float(order.limit_price),
                    time_in_force=tif,
                )
            else:
                raise InvalidOrderError(
                    f"Order type {order.order_type} not yet implemented", order_id=order.id  # type: ignore
                )

            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.submit_order(request)
            )

            updated = order.model_copy(
                update={
                    "broker_order_id": str(result.id),
                    "status": _STATUS_MAP.get(str(result.status), OrderStatus.SUBMITTED),
                }
            )

            log.info(
                "order_submitted",
                order_id=order.id,
                broker_order_id=str(result.id),
                symbol=order.symbol,
                side=order.side.value,
                quantity=float(order.quantity),
            )
            return updated

        except InvalidOrderError:
            raise
        except Exception as exc:
            if "forbidden" in str(exc).lower() or "unauthorized" in str(exc).lower():
                raise BrokerAuthenticationError(f"Alpaca auth failed: {exc}") from exc
            if "insufficient" in str(exc).lower():
                from src.core.exceptions import InsufficientFundsError
                raise InsufficientFundsError(f"Insufficient funds: {exc}") from exc
            raise OrderRejectedError(
                str(exc), order_id=order.id, broker_reason=str(exc)
            ) from exc

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by broker order ID."""
        try:
            client = self._get_client()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.cancel_order_by_id(order_id)
            )
            log.info("order_cancelled", broker_order_id=order_id)
            return True
        except Exception as exc:
            log.warning("cancel_order_failed", broker_order_id=order_id, error=str(exc))
            return False

    async def get_order_status(self, broker_order_id: str) -> OrderStatus:
        """Fetch order status from Alpaca."""
        try:
            client = self._get_client()
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_order_by_id(broker_order_id)
            )
            return _STATUS_MAP.get(str(result.status), OrderStatus.SUBMITTED)
        except Exception as exc:
            log.error("get_order_status_failed", broker_order_id=broker_order_id, error=str(exc))
            raise BrokerConnectionError(f"Cannot fetch order status: {exc}") from exc

    async def get_fills(self, broker_order_id: str) -> list[Fill]:
        """Fetch fills for a completed order."""
        try:
            from alpaca.trading.requests import GetOrdersRequest  # type: ignore

            client = self._get_client()
            order = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_order_by_id(broker_order_id)
            )

            if not order.filled_at:
                return []

            return [
                Fill(
                    order_id=broker_order_id,
                    timestamp=order.filled_at,
                    symbol=str(order.symbol),
                    side=OrderSide.BUY if str(order.side) == "buy" else OrderSide.SELL,
                    quantity=Decimal(str(order.filled_qty or 0)),
                    price=Decimal(str(order.filled_avg_price or 0)),
                )
            ]
        except Exception as exc:
            log.error("get_fills_failed", broker_order_id=broker_order_id, error=str(exc))
            return []

    async def get_account(self) -> Portfolio:
        """Fetch current account state from Alpaca."""
        try:
            from alpaca.trading.requests import GetAssetsRequest  # type: ignore

            client = self._get_client()
            account = await asyncio.get_event_loop().run_in_executor(
                None, client.get_account
            )
            positions_raw = await asyncio.get_event_loop().run_in_executor(
                None, client.get_all_positions
            )

            positions = {}
            for pos in positions_raw:
                positions[str(pos.symbol)] = Position(
                    symbol=str(pos.symbol),
                    quantity=Decimal(str(pos.qty)),
                    average_cost=Decimal(str(pos.avg_entry_price or 0)),
                    current_price=Decimal(str(pos.current_price or 0)),
                )

            return Portfolio(
                cash=Decimal(str(account.cash)),
                total_equity=Decimal(str(account.equity)),
                daily_pnl=Decimal(str(account.unrealized_pl or 0)),
                positions=positions,
            )
        except Exception as exc:
            raise BrokerConnectionError(f"Cannot fetch account: {exc}") from exc

    async def get_position(self, symbol: str) -> Position | None:
        """Fetch a single position."""
        try:
            client = self._get_client()
            pos = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.get_open_position(symbol)
            )
            if pos is None:
                return None
            return Position(
                symbol=symbol,
                quantity=Decimal(str(pos.qty)),
                average_cost=Decimal(str(pos.avg_entry_price or 0)),
                current_price=Decimal(str(pos.current_price or 0)),
            )
        except Exception:
            return None

    async def get_buying_power(self) -> Decimal:
        """Return buying power from account."""
        try:
            client = self._get_client()
            account = await asyncio.get_event_loop().run_in_executor(None, client.get_account)
            return Decimal(str(account.buying_power or 0))
        except Exception as exc:
            raise BrokerConnectionError(f"Cannot fetch buying power: {exc}") from exc

    async def health_check(self) -> bool:
        """Verify Alpaca connectivity."""
        try:
            client = self._get_client()
            await asyncio.get_event_loop().run_in_executor(None, client.get_account)
            return True
        except Exception:
            return False

    async def close_all_positions(self) -> list[Order]:
        """Emergency close all positions."""
        try:
            client = self._get_client()
            await asyncio.get_event_loop().run_in_executor(
                None, client.close_all_positions
            )
            log.critical("emergency_close_all_positions")
            return []
        except Exception as exc:
            raise BrokerConnectionError(f"Emergency close failed: {exc}") from exc
