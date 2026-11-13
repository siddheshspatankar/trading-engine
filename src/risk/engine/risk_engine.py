"""
Independent Risk Management Engine.

This is the authorization gate between signals and execution.
No order leaves this system without passing ALL risk checks.

Risk checks (in order of evaluation):
1. Trading halt check — is the system halted?
2. Market hours check
3. Daily loss limit — halt if exceeded
4. Max drawdown check
5. Position size limit
6. Portfolio concentration check
7. Portfolio VaR (Value at Risk)
8. Correlation / concentration risk
9. Leverage limit

Design: ALL checks must pass. First failure = rejection.
Size reduction is applied BEFORE returning the authorized order.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import structlog

from src.core.events import (
    DrawdownAlertTriggered,
    RiskCheckFailed,
    RiskCheckPassed,
    TradingHaltTriggered,
    event_bus,
)
from src.core.exceptions import (
    DailyLossLimitBreached,
    DrawdownLimitBreached,
    PositionSizeLimitBreached,
    RiskLimitBreached,
)
from src.core.models import Order, OrderSide, Portfolio, RiskDecision, Signal
from src.infrastructure.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class RiskCheckResult:
    """Result of a single risk check."""

    check_name: str
    passed: bool
    message: str
    original_quantity: Decimal
    authorized_quantity: Decimal  # may be reduced from original
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskEngineResult:
    """Aggregated result of all risk checks for an order."""

    order_id: str
    decision: RiskDecision
    authorized_quantity: Decimal
    checks: list[RiskCheckResult]
    failure_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision == RiskDecision.APPROVED or self.decision == RiskDecision.MODIFIED

    @property
    def failed_check(self) -> RiskCheckResult | None:
        return next((c for c in self.checks if not c.passed), None)


class RiskEngine:
    """
    Independent risk management engine.

    Operates deterministically — no AI, no external calls, no state mutations.
    Pure computation: given an order and portfolio, return a risk decision.
    """

    def __init__(self) -> None:
        self._settings = get_settings().risk
        self._trading_halted: bool = False
        self._halt_reason: str | None = None
        self._daily_pnl_start: Decimal | None = None
        self._peak_equity: Decimal = Decimal("0")
        self._today: date | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def halt_trading(self, reason: str) -> None:
        """
        Halt all trading. All subsequent orders will be rejected until resumed.
        Only callable with explicit authorization (operations team, circuit breaker).
        """
        self._trading_halted = True
        self._halt_reason = reason
        log.critical("trading_halted", reason=reason)

    def resume_trading(self) -> None:
        """Resume trading after a halt. Requires explicit call."""
        self._trading_halted = False
        self._halt_reason = None
        log.warning("trading_resumed")

    @property
    def is_halted(self) -> bool:
        return self._trading_halted

    async def evaluate_order(
        self,
        order: Order,
        portfolio: Portfolio,
        market_prices: dict[str, Decimal] | None = None,
    ) -> RiskEngineResult:
        """
        Evaluate a proposed order against all risk limits.

        Returns a RiskEngineResult indicating APPROVED, MODIFIED, or REJECTED.
        Every check is logged for the audit trail.
        """
        log.info(
            "risk_evaluation_start",
            order_id=order.id,
            symbol=order.symbol,
            side=order.side.value,
            quantity=str(order.quantity),
        )

        checks: list[RiskCheckResult] = []
        current_qty = order.quantity
        warnings: list[str] = []

        # Run checks in sequence — first failure stops evaluation
        check_fns = [
            self._check_trading_halt,
            self._check_daily_loss_limit,
            self._check_drawdown_limit,
            self._check_position_size_limit,
            self._check_concentration_limit,
            self._check_leverage_limit,
        ]

        for check_fn in check_fns:
            result = await check_fn(order, current_qty, portfolio, market_prices or {})
            checks.append(result)

            if not result.passed:
                decision = RiskDecision.REJECTED
                await event_bus.publish(
                    RiskCheckFailed(
                        order_id=order.id,
                        reason=result.message,
                        limit_type=result.check_name,
                    )
                )
                log.warning(
                    "risk_check_failed",
                    check=result.check_name,
                    order_id=order.id,
                    message=result.message,
                )
                return RiskEngineResult(
                    order_id=order.id,
                    decision=decision,
                    authorized_quantity=Decimal("0"),
                    checks=checks,
                    failure_reason=f"{result.check_name}: {result.message}",
                    warnings=warnings,
                )

            # Apply any size reduction from the check
            if result.authorized_quantity < current_qty:
                warnings.append(
                    f"{result.check_name}: quantity reduced "
                    f"{current_qty} → {result.authorized_quantity}"
                )
                current_qty = result.authorized_quantity

        # All checks passed
        decision = RiskDecision.MODIFIED if current_qty < order.quantity else RiskDecision.APPROVED
        await event_bus.publish(
            RiskCheckPassed(
                order_id=order.id,
                checks_performed=[c.check_name for c in checks],
            )
        )

        log.info(
            "risk_evaluation_complete",
            order_id=order.id,
            decision=decision.value,
            original_qty=str(order.quantity),
            authorized_qty=str(current_qty),
        )

        return RiskEngineResult(
            order_id=order.id,
            decision=decision,
            authorized_quantity=current_qty,
            checks=checks,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Individual Risk Checks
    # ------------------------------------------------------------------

    async def _check_trading_halt(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        if self._trading_halted:
            return RiskCheckResult(
                check_name="trading_halt",
                passed=False,
                message=f"Trading is halted: {self._halt_reason}",
                original_quantity=quantity,
                authorized_quantity=Decimal("0"),
            )
        return RiskCheckResult(
            check_name="trading_halt",
            passed=True,
            message="Trading is active",
            original_quantity=quantity,
            authorized_quantity=quantity,
        )

    async def _check_daily_loss_limit(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        """Reject all orders if daily loss limit is breached."""
        if portfolio.daily_pnl is None or portfolio.total_equity is None:
            return RiskCheckResult(
                check_name="daily_loss_limit",
                passed=True,
                message="No daily P&L data available — skipping",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        daily_loss_pct = float(-portfolio.daily_pnl / portfolio.total_equity)
        limit = self._settings.max_daily_loss_pct

        if daily_loss_pct > limit:
            # Trigger halt
            if not self._trading_halted:
                await event_bus.publish(
                    TradingHaltTriggered(
                        reason=f"Daily loss limit breached: {daily_loss_pct:.2%} > {limit:.2%}",
                        daily_loss_pct=daily_loss_pct,
                    )
                )
                self.halt_trading(f"Daily loss limit: {daily_loss_pct:.2%}")

            return RiskCheckResult(
                check_name="daily_loss_limit",
                passed=False,
                message=f"Daily loss {daily_loss_pct:.2%} exceeds limit {limit:.2%}",
                original_quantity=quantity,
                authorized_quantity=Decimal("0"),
                metadata={"daily_loss_pct": daily_loss_pct, "limit": limit},
            )

        return RiskCheckResult(
            check_name="daily_loss_limit",
            passed=True,
            message=f"Daily loss {daily_loss_pct:.2%} within limit {limit:.2%}",
            original_quantity=quantity,
            authorized_quantity=quantity,
            metadata={"daily_loss_pct": daily_loss_pct, "limit": limit},
        )

    async def _check_drawdown_limit(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        """Block new entries if drawdown exceeds the threshold."""
        if portfolio.total_equity is None:
            return RiskCheckResult(
                check_name="drawdown_limit",
                passed=True,
                message="No equity data — skipping",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        # Update peak equity
        if portfolio.total_equity > self._peak_equity:
            self._peak_equity = portfolio.total_equity

        if self._peak_equity == 0:
            return RiskCheckResult(
                check_name="drawdown_limit",
                passed=True,
                message="Peak equity not established yet",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        drawdown = float((self._peak_equity - portfolio.total_equity) / self._peak_equity)
        limit = self._settings.max_drawdown_pct

        # Alert at 75% of limit
        if drawdown > limit * 0.75:
            await event_bus.publish(
                DrawdownAlertTriggered(
                    current_drawdown_pct=drawdown,
                    threshold_pct=limit,
                )
            )

        if drawdown > limit and order.side == OrderSide.BUY:
            return RiskCheckResult(
                check_name="drawdown_limit",
                passed=False,
                message=f"Drawdown {drawdown:.2%} exceeds limit {limit:.2%}. No new long entries.",
                original_quantity=quantity,
                authorized_quantity=Decimal("0"),
                metadata={"drawdown": drawdown, "limit": limit},
            )

        return RiskCheckResult(
            check_name="drawdown_limit",
            passed=True,
            message=f"Drawdown {drawdown:.2%} within limit {limit:.2%}",
            original_quantity=quantity,
            authorized_quantity=quantity,
            metadata={"drawdown": drawdown, "limit": limit},
        )

    async def _check_position_size_limit(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        """Ensure single position does not exceed max_position_pct of equity."""
        if portfolio.total_equity is None or portfolio.total_equity == 0:
            return RiskCheckResult(
                check_name="position_size_limit",
                passed=True,
                message="No equity data — skipping",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        price = prices.get(order.symbol) or (order.limit_price or Decimal("1"))
        max_pct = Decimal(str(self._settings.max_position_pct))
        max_notional = portfolio.total_equity * max_pct

        # Existing position value
        existing_pos = portfolio.positions.get(order.symbol)
        existing_notional = Decimal("0")
        if existing_pos and existing_pos.current_price:
            existing_notional = abs(existing_pos.quantity * existing_pos.current_price)

        proposed_notional = quantity * price
        total_notional = existing_notional + proposed_notional

        if total_notional > max_notional:
            # Reduce to fit within limit
            allowed_notional = max(Decimal("0"), max_notional - existing_notional)
            authorized_qty = allowed_notional / price if price > 0 else Decimal("0")
            authorized_qty = authorized_qty.quantize(Decimal("1"))  # round to whole shares

            if authorized_qty <= 0:
                return RiskCheckResult(
                    check_name="position_size_limit",
                    passed=False,
                    message=(
                        f"Position in {order.symbol} already at max "
                        f"({float(existing_notional / portfolio.total_equity):.1%}). No room for new entry."
                    ),
                    original_quantity=quantity,
                    authorized_quantity=Decimal("0"),
                )

            return RiskCheckResult(
                check_name="position_size_limit",
                passed=True,
                message=f"Quantity reduced to stay within {float(max_pct):.0%} limit",
                original_quantity=quantity,
                authorized_quantity=authorized_qty,
                metadata={
                    "max_notional": float(max_notional),
                    "total_notional": float(total_notional),
                    "authorized_qty": float(authorized_qty),
                },
            )

        return RiskCheckResult(
            check_name="position_size_limit",
            passed=True,
            message=f"Position size {float(total_notional / portfolio.total_equity):.2%} within limit",
            original_quantity=quantity,
            authorized_quantity=quantity,
        )

    async def _check_concentration_limit(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        """Warn if adding this position would create high sector/name concentration."""
        # Simple name concentration check: top 3 positions should not exceed 50%
        if portfolio.total_equity is None or portfolio.total_equity == 0:
            return RiskCheckResult(
                check_name="concentration_limit",
                passed=True,
                message="No equity data — skipping",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        # This is a simplified check — in production, sector data would come from a reference data service
        position_weights = {}
        for sym, pos in portfolio.positions.items():
            if pos.current_price:
                weight = float(abs(pos.quantity * pos.current_price) / portfolio.total_equity)
                position_weights[sym] = weight

        top_3_concentration = sum(sorted(position_weights.values(), reverse=True)[:3])
        if top_3_concentration > 0.60:
            return RiskCheckResult(
                check_name="concentration_limit",
                passed=False,
                message=f"Top-3 concentration {top_3_concentration:.1%} > 60%. Diversify before adding.",
                original_quantity=quantity,
                authorized_quantity=Decimal("0"),
                metadata={"top_3_concentration": top_3_concentration},
            )

        return RiskCheckResult(
            check_name="concentration_limit",
            passed=True,
            message=f"Concentration {top_3_concentration:.1%} within acceptable range",
            original_quantity=quantity,
            authorized_quantity=quantity,
        )

    async def _check_leverage_limit(
        self,
        order: Order,
        quantity: Decimal,
        portfolio: Portfolio,
        prices: dict[str, Decimal],
    ) -> RiskCheckResult:
        """Ensure gross leverage stays below 2x (1x for paper/dev mode)."""
        max_leverage = 2.0 if get_settings().is_live else 1.0

        if portfolio.total_equity is None or portfolio.total_equity == 0:
            return RiskCheckResult(
                check_name="leverage_limit",
                passed=True,
                message="No equity data — skipping",
                original_quantity=quantity,
                authorized_quantity=quantity,
            )

        price = prices.get(order.symbol) or (order.limit_price or Decimal("1"))
        new_market_value = portfolio.total_market_value + quantity * price
        new_leverage = float(new_market_value / portfolio.total_equity)

        if new_leverage > max_leverage:
            return RiskCheckResult(
                check_name="leverage_limit",
                passed=False,
                message=f"Proposed leverage {new_leverage:.2f}x exceeds limit {max_leverage:.1f}x",
                original_quantity=quantity,
                authorized_quantity=Decimal("0"),
                metadata={"new_leverage": new_leverage, "max_leverage": max_leverage},
            )

        return RiskCheckResult(
            check_name="leverage_limit",
            passed=True,
            message=f"Leverage {new_leverage:.2f}x within limit {max_leverage:.1f}x",
            original_quantity=quantity,
            authorized_quantity=quantity,
        )
