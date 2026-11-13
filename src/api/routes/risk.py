"""Risk management endpoints: limits, kill switch, decision audit log."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query

from src.api.state import state
from src.infrastructure.config import get_settings

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/status")
async def get_risk_status() -> dict:
    """Current risk engine status and configured limits."""
    engine = state.risk_engine
    settings = get_settings()
    return {
        "trading_halted": engine.is_halted,
        "halt_reason": engine._halt_reason,
        "trading_mode": settings.env,
        "limits": {
            "max_position_pct": settings.risk.max_position_pct,
            "max_drawdown_pct": settings.risk.max_drawdown_pct,
            "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
            "max_portfolio_var_pct": settings.risk.max_portfolio_var_pct,
            "max_leverage": 2.0 if settings.is_live else 1.0,
            "max_top3_concentration_pct": 0.60,
        },
    }


@router.get("/log")
async def get_risk_log(limit: int = Query(50, ge=1, le=200)) -> dict:
    """Most recent risk-engine decisions (newest first)."""
    return {"entries": [asdict(e) for e in state.risk_log[:limit]]}


@router.post("/halt")
async def halt_trading(reason: str = Query("Manual halt via dashboard", max_length=200)) -> dict:
    """Kill switch: every new order is rejected until trading is resumed."""
    state.risk_engine.halt_trading(reason)
    return {"status": "halted", "reason": reason}


@router.post("/resume")
async def resume_trading() -> dict:
    state.risk_engine.resume_trading()
    return {"status": "trading_resumed"}
