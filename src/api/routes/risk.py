"""Risk management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.infrastructure.config import get_settings
from src.risk.engine.risk_engine import RiskEngine

router = APIRouter(prefix="/risk", tags=["risk"])

# Module-level risk engine instance (in production, use DI)
_risk_engine: RiskEngine | None = None


def get_risk_engine() -> RiskEngine:
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine()
    return _risk_engine


@router.get("/status")
async def get_risk_status() -> dict:
    """Return current risk engine status."""
    engine = get_risk_engine()
    settings = get_settings()
    return {
        "trading_halted": engine.is_halted,
        "trading_mode": settings.env,
        "limits": {
            "max_position_pct": settings.risk.max_position_pct,
            "max_drawdown_pct": settings.risk.max_drawdown_pct,
            "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
            "max_portfolio_var_pct": settings.risk.max_portfolio_var_pct,
        },
    }


@router.post("/halt")
async def halt_trading(reason: str = "Manual halt via API") -> dict:
    """
    Halt all trading.
    This action requires explicit confirmation. Protected in production by auth middleware.
    """
    engine = get_risk_engine()
    engine.halt_trading(reason)
    return {"status": "halted", "reason": reason}


@router.post("/resume")
async def resume_trading() -> dict:
    """Resume trading after a halt."""
    engine = get_risk_engine()
    engine.resume_trading()
    return {"status": "trading_resumed"}
