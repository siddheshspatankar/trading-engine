"""Portfolio and position endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PortfolioResponse(BaseModel):
    cash: float
    total_equity: float | None
    positions: dict
    num_positions: int
    daily_pnl: float | None


@router.get("/", response_model=PortfolioResponse)
async def get_portfolio() -> PortfolioResponse:
    """Return current portfolio state."""
    # TODO: Inject broker adapter via dependency injection
    return PortfolioResponse(
        cash=0.0,
        total_equity=None,
        positions={},
        num_positions=0,
        daily_pnl=None,
    )


@router.get("/positions")
async def get_positions() -> list[dict]:
    """Return all open positions."""
    return []


@router.get("/positions/{symbol}")
async def get_position(symbol: str) -> dict:
    """Return position for a specific symbol."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No open position for {symbol}",
    )
