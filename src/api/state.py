"""
Shared, process-wide engine state for the API.

The dashboard trades against an in-memory paper broker — never a real brokerage
account — so it is safe to expose in paper/development mode. State resets when the
process restarts (e.g. when a free Render instance spins down).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.broker.adapters.paper_broker import PaperBrokerAdapter
from src.core.models import Order
from src.market_data.service import MarketDataService
from src.risk.engine.risk_engine import RiskEngine

INITIAL_CAPITAL = Decimal("100000")
MAX_RISK_LOG = 200


@dataclass
class RiskLogEntry:
    """One risk-engine decision, kept for the dashboard's audit view."""

    timestamp: str
    order_id: str
    symbol: str
    side: str
    requested_qty: float
    authorized_qty: float
    decision: str
    reason: str | None
    warnings: list[str]
    checks: list[dict[str, Any]] = field(default_factory=list)


class EngineState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.broker = PaperBrokerAdapter(initial_capital=INITIAL_CAPITAL)
        self.risk_engine = RiskEngine()
        self.market = MarketDataService()
        self.risk_log: list[RiskLogEntry] = []
        self.order_meta: dict[str, dict[str, Any]] = {}  # order_id -> risk decision summary
        self.rejected_orders: list[Order] = []  # blocked by risk, never reached the broker
        self.started_at = datetime.now(timezone.utc)

    def record_risk(self, entry: RiskLogEntry) -> None:
        self.risk_log.insert(0, entry)
        del self.risk_log[MAX_RISK_LOG:]


state = EngineState()
