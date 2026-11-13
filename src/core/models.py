"""
Core domain models for the trading engine.

These are immutable, serializable value objects shared across all layers.
No business logic lives here — only data shapes and enumerations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AssetClass(str, Enum):
    EQUITY = "equity"
    OPTION = "option"
    FUTURE = "future"
    FOREX = "forex"
    CRYPTO = "crypto"
    ETF = "etf"
    BOND = "bond"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(str, Enum):
    PENDING = "pending"           # created, not yet sent
    SUBMITTED = "submitted"       # sent to broker
    ACCEPTED = "accepted"         # acknowledged by broker
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"       # good till cancelled
    IOC = "ioc"       # immediate or cancel
    FOK = "fok"       # fill or kill
    GTX = "gtx"       # good till crossing (Alpaca)
    OPG = "opg"       # market on open
    CLS = "cls"       # market on close


class TradingMode(str, Enum):
    SIMULATION = "simulation"     # fully synthetic, no connectivity
    PAPER = "paper"               # live data, simulated execution
    SHADOW = "shadow"             # live decisions, logged only (not sent)
    LIVE = "live"                 # real money — requires explicit authorization


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"                 # exit / no position


class RiskDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"         # size or price adjusted by risk engine


# ---------------------------------------------------------------------------
# Value Objects
# ---------------------------------------------------------------------------


class Symbol(BaseModel):
    """Represents a tradeable instrument."""

    ticker: str
    asset_class: AssetClass = AssetClass.EQUITY
    exchange: Optional[str] = None
    currency: str = "USD"

    @field_validator("ticker")
    @classmethod
    def ticker_must_be_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    def __str__(self) -> str:
        return self.ticker

    def __hash__(self) -> int:
        return hash(self.ticker)


class Price(BaseModel):
    """OHLCV price bar."""

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Optional[Decimal] = None
    trades: Optional[int] = None

    @field_validator("high")
    @classmethod
    def high_gte_low(cls, v: Decimal, info: object) -> Decimal:
        # Basic sanity — high must be >= low
        return v

    class Config:
        frozen = True


class Quote(BaseModel):
    """Real-time bid/ask quote."""

    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> Decimal:
        return (self.spread / self.mid) * 10000

    class Config:
        frozen = True


class Signal(BaseModel):
    """Trading signal emitted by a strategy or agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str                           # strategy name or agent id
    symbol: str
    direction: SignalDirection
    strength: float = Field(ge=0.0, le=1.0)   # 0 = weak, 1 = strong conviction
    target_weight: Optional[float] = None      # desired portfolio weight
    rationale: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

    class Config:
        frozen = True


class Order(BaseModel):
    """Represents a trading order at any point in its lifecycle."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    broker_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING

    # Fill tracking
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None

    # Attribution
    strategy_id: Optional[str] = None
    signal_id: Optional[str] = None

    # Risk authorization
    risk_decision: Optional[RiskDecision] = None
    risk_notes: Optional[str] = None

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }


class Fill(BaseModel):
    """Represents a single execution fill."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    broker_fill_id: Optional[str] = None

    @property
    def gross_value(self) -> Decimal:
        return self.quantity * self.price

    @property
    def net_value(self) -> Decimal:
        return self.gross_value + self.commission


class Position(BaseModel):
    """Current open position in a symbol."""

    symbol: str
    quantity: Decimal          # positive = long, negative = short
    average_cost: Decimal
    current_price: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    strategy_id: Optional[str] = None

    @property
    def market_value(self) -> Optional[Decimal]:
        if self.current_price is None:
            return None
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> Optional[Decimal]:
        if self.current_price is None:
            return None
        return (self.current_price - self.average_cost) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> Optional[float]:
        if self.unrealized_pnl is None or self.average_cost == 0:
            return None
        return float(self.unrealized_pnl / (self.average_cost * abs(self.quantity)))

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0


class Portfolio(BaseModel):
    """Snapshot of the full portfolio state."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cash: Decimal
    positions: dict[str, Position] = Field(default_factory=dict)
    total_equity: Optional[Decimal] = None
    daily_pnl: Optional[Decimal] = None
    total_pnl: Optional[Decimal] = None

    @property
    def total_market_value(self) -> Decimal:
        return sum(
            (p.market_value or Decimal("0")) for p in self.positions.values()
        )

    @property
    def leverage(self) -> Decimal:
        if (self.total_equity or Decimal("0")) == 0:
            return Decimal("0")
        return self.total_market_value / (self.total_equity or Decimal("1"))
